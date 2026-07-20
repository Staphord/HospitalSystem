"""Administration business logic (users, config, permissions)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.admin import (
    Bed,
    Department,
    FeeSchedule,
    InsuranceProvider,
    RolePermission,
)
from app.models.master import Tenant, TenantRole
from app.models.user import User
from app.services import audit_service
from app.services.keycloak_admin import (
    create_keycloak_user,
    create_local_user,
    create_realm_role,
    delete_keycloak_user,
    delete_local_user,
    delete_realm_role,
    ensure_roles,
    get_realm_roles,
    logout_user_sessions,
    replace_user_roles,
    set_user_attribute,
    set_user_password,
    update_keycloak_user,
    update_local_user,
    update_realm_role,
)
from app.services.roles import (
    DEFAULT_ROLE_ACTIONS,
    DEFAULT_ROLE_MODULES,
    SYSTEM_ROLES,
    keycloak_roles_for,
    validate_assignable_role,
)

logger = logging.getLogger("admin_service.admin")

DEFAULT_DEPARTMENTS = [
    ("Reception", "reception"),
    ("Triage", "triage"),
    ("Consultation", "consultation"),
    ("Laboratory", "laboratory"),
    ("Radiology", "radiology"),
    ("Pharmacy", "pharmacy"),
    ("Ward", "ward"),
    ("ICU", "icu"),
    ("Billing", "billing"),
    ("Admin", "admin"),
]


def _user_snapshot(u: User) -> dict[str, Any]:
    return {
        "keycloak_sub": u.keycloak_sub,
        "username": u.username,
        "full_name": u.full_name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "department_id": str(u.department_id) if u.department_id else None,
        "phone": u.phone,
        "force_password_change": u.force_password_change,
        "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
    }


def count_active_hospital_admins(db: Session, hospital_id: str, exclude_sub: str | None = None) -> int:
    q = db.query(User).filter(
        User.hospital_id == hospital_id,
        User.role == "hospital_admin",
        User.is_active.is_(True),
        User.deleted_at.is_(None),
    )
    if exclude_sub:
        q = q.filter(User.keycloak_sub != exclude_sub)
    return q.count()


def ensure_not_last_admin(
    db: Session,
    hospital_id: str,
    user: User,
    *,
    demoting: bool = False,
    deactivating: bool = False,
    deleting: bool = False,
) -> None:
    if user.role != "hospital_admin":
        return
    if not (demoting or deactivating or deleting):
        return
    remaining = count_active_hospital_admins(db, hospital_id, exclude_sub=user.keycloak_sub)
    if remaining < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove or demote the last active hospital_admin",
        )


def list_users(
    db: Session,
    hospital_id: str,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    department_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "username",
    include_deleted: bool = False,
) -> tuple[list[User], int]:
    query = db.query(User).filter(User.hospital_id == hospital_id)
    if not include_deleted:
        query = query.filter(User.deleted_at.is_(None))
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    if department_id:
        query = query.filter(User.department_id == department_id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.username.ilike(like),
                User.email.ilike(like),
                User.full_name.ilike(like),
            )
        )
    total = query.count()
    sort_col = {
        "username": User.username,
        "email": User.email,
        "role": User.role,
        "full_name": User.full_name,
    }.get(sort, User.username)
    rows = query.order_by(sort_col.asc().nullslast()).offset(offset).limit(min(limit, 200)).all()
    return rows, total


def get_user(db: Session, hospital_id: str, sub: str) -> User:
    user = db.query(User).filter(User.keycloak_sub == sub, User.hospital_id == hospital_id).first()
    if not user or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this hospital")
    return user


async def create_user(
    db: Session,
    master_db: Session,
    *,
    tenant_id: str,
    realm: str,
    username: str,
    password: str,
    email: str,
    full_name: str,
    role: str,
    actor_sub: str,
    department_id: uuid.UUID | None = None,
    phone: str | None = None,
    ip_address: str | None = None,
) -> User:
    validate_assignable_role(role)
    roles_to_ensure = list(SYSTEM_ROLES | {role})
    await ensure_roles(roles_to_ensure, realm=realm)
    kc_roles = keycloak_roles_for(role)

    dup = (
        db.query(User)
        .filter(
            User.hospital_id == tenant_id,
            User.deleted_at.is_(None),
            or_(User.username == username, User.email == email),
        )
        .first()
    )
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Username or email already exists")

    try:
        kc_sub = await create_keycloak_user(
            username=username,
            password=password,
            email=email,
            roles=kc_roles,
            full_name=full_name or None,
            realm=realm,
        )
        await set_user_password(kc_sub, password, realm=realm, temporary=True)
    except Exception as e:
        if "409" in str(e) or "Conflict" in str(e):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Username or email already exists in Keycloak") from e
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to create user in Keycloak: {e}") from e

    await set_user_attribute(kc_sub, "tenant_id", tenant_id, realm=realm)

    expires = datetime.now(timezone.utc) + timedelta(days=90)
    user = create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=username,
        full_name=full_name or None,
        email=email,
        role=role,
        hospital_id=tenant_id,
        force_password_change=True,
        department_id=department_id,
        phone=phone,
        password_expires_at=expires,
    )

    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="CREATE",
        table_name="users",
        record_id=kc_sub,
        new_values=_user_snapshot(user),
        ip_address=ip_address,
    )
    return user


async def update_user(
    db: Session,
    *,
    tenant_id: str,
    realm: str,
    sub: str,
    actor_sub: str,
    username: str | None = None,
    email: str | None = None,
    full_name: str | None = None,
    password: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    force_password_change: bool | None = None,
    department_id: uuid.UUID | None = None,
    phone: str | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
) -> User:
    user = get_user(db, tenant_id, sub)
    old = _user_snapshot(user)

    if role is not None:
        validate_assignable_role(role)
        if role != user.role:
            ensure_not_last_admin(db, tenant_id, user, demoting=True)

    if is_active is False and user.is_active:
        ensure_not_last_admin(db, tenant_id, user, deactivating=True)

    if username is not None or email is not None or (full_name is not None and full_name.strip()) or is_active is not None:
        await update_keycloak_user(
            user.keycloak_sub,
            username=username,
            email=email,
            full_name=full_name,
            enabled=is_active,
            realm=realm,
        )
    if password is not None:
        await set_user_password(user.keycloak_sub, password, realm=realm, temporary=bool(force_password_change))
    if role is not None:
        await ensure_roles(list(SYSTEM_ROLES | {role}), realm=realm)
        target_roles = keycloak_roles_for(role)
        try:
            await replace_user_roles(user.keycloak_sub, target_roles, realm=realm)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to update Keycloak roles: {e}") from e

    updated = update_local_user(
        db=db,
        keycloak_sub=sub,
        username=username,
        full_name=full_name,
        email=email,
        role=role,
        is_active=is_active,
        force_password_change=force_password_change,
        department_id=department_id,
        phone=phone,
    )
    assert updated is not None

    if is_active is False:
        try:
            await logout_user_sessions(user.keycloak_sub, realm=realm)
        except Exception:
            logger.exception("Failed to revoke sessions for %s", sub)

    new_vals = _user_snapshot(updated)
    if reason:
        new_vals["reason"] = reason
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="users",
        record_id=sub,
        old_values=old,
        new_values=new_vals,
        ip_address=ip_address,
    )
    return updated


async def delete_user(
    db: Session,
    *,
    tenant_id: str,
    realm: str,
    sub: str,
    actor_sub: str,
    hard: bool = False,
    ip_address: str | None = None,
) -> None:
    if sub == actor_sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    user = get_user(db, tenant_id, sub)
    ensure_not_last_admin(db, tenant_id, user, deleting=True)
    old = _user_snapshot(user)

    if hard:
        kc_deleted = await delete_keycloak_user(user.username or user.email or "", realm=realm)
        if not kc_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in Keycloak")
        delete_local_user(db, sub)
        action = "DELETE"
        new_vals = None
    else:
        await update_keycloak_user(user.keycloak_sub, enabled=False, realm=realm)
        try:
            await logout_user_sessions(user.keycloak_sub, realm=realm)
        except Exception:
            logger.exception("Failed to revoke sessions for soft-delete %s", sub)
        update_local_user(
            db,
            keycloak_sub=sub,
            is_active=False,
            deleted_at=datetime.now(timezone.utc),
        )
        action = "SOFT_DELETE"
        new_vals = {"is_active": False, "deleted_at": datetime.now(timezone.utc).isoformat()}

    audit_service.log_change(
        db,
        user_id=actor_sub,
        action=action,
        table_name="users",
        record_id=sub,
        old_values=old,
        new_values=new_vals,
        ip_address=ip_address,
    )


async def list_assignable_roles(master_db: Session, tenant_id: str, realm: str) -> list[str]:
    names: set[str] = set(SYSTEM_ROLES)
    try:
        kc = await get_realm_roles(realm)
        for r in kc:
            n = r.get("name")
            if n and not str(n).startswith("default-roles-") and n not in ("offline_access", "uma_authorization"):
                names.add(n)
    except Exception:
        logger.exception("Failed listing Keycloak roles for assignable set")
    for tr in master_db.query(TenantRole).filter(TenantRole.tenant_id == tenant_id).all():
        names.add(tr.name)
    try:
        from sqlalchemy import text

        rows = master_db.execute(text("SELECT name FROM global_roles")).fetchall()
        for row in rows:
            names.add(row[0])
    except Exception:
        pass
    names -= {"super_admin"}
    return sorted(names)


# ---- Permissions (FR-54) ----


def seed_default_permissions(db: Session) -> None:
    for role, modules in DEFAULT_ROLE_MODULES.items():
        existing = db.query(RolePermission).filter(RolePermission.role_name == role).first()
        if existing:
            continue
        db.add(
            RolePermission(
                permission_id=uuid.uuid4(),
                role_name=role,
                modules=modules,
                actions=DEFAULT_ROLE_ACTIONS.get(role, ["read"]),
            )
        )
    db.commit()


def list_permissions(db: Session) -> list[RolePermission]:
    seed_default_permissions(db)
    return db.query(RolePermission).order_by(RolePermission.role_name).all()


def upsert_permission(
    db: Session,
    role_name: str,
    modules: list[str],
    actions: list[str],
    actor_sub: str,
    ip_address: str | None = None,
) -> RolePermission:
    seed_default_permissions(db)
    row = db.query(RolePermission).filter(RolePermission.role_name == role_name).first()
    old = None
    if row:
        old = {"role_name": row.role_name, "modules": row.modules, "actions": row.actions}
        row.modules = modules
        row.actions = actions
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = RolePermission(
            permission_id=uuid.uuid4(),
            role_name=role_name,
            modules=modules,
            actions=actions,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE" if old else "CREATE",
        table_name="role_permissions",
        record_id=role_name,
        old_values=old,
        new_values={"role_name": role_name, "modules": modules, "actions": actions},
        ip_address=ip_address,
    )
    return row


# ---- Hospital profile / departments / fees / beds / insurance (FR-55) ----


def get_hospital_profile(master_db: Session, tenant_id: str) -> Tenant:
    tenant = master_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def update_hospital_profile(
    master_db: Session,
    tenant_db: Session,
    tenant_id: str,
    actor_sub: str,
    data: dict[str, Any],
    ip_address: str | None = None,
) -> Tenant:
    tenant = get_hospital_profile(master_db, tenant_id)
    old = {
        "hospital_name": tenant.hospital_name,
        "timezone": tenant.timezone,
        "currency": tenant.currency,
        "date_format": getattr(tenant, "date_format", None),
        "logo_url": getattr(tenant, "logo_url", None),
        "primary_contact_name": getattr(tenant, "primary_contact_name", None),
        "primary_contact_email": getattr(tenant, "primary_contact_email", None),
        "primary_contact_phone": getattr(tenant, "primary_contact_phone", None),
        "address": getattr(tenant, "address", None),
        "city": getattr(tenant, "city", None),
        "country": getattr(tenant, "country", None),
    }
    allowed = {
        "hospital_name",
        "timezone",
        "currency",
        "date_format",
        "logo_url",
        "primary_contact_name",
        "primary_contact_email",
        "primary_contact_phone",
        "address",
        "city",
        "country",
        "billing_email",
    }
    for k, v in data.items():
        if k in allowed and v is not None and hasattr(tenant, k):
            setattr(tenant, k, v)
    master_db.commit()
    master_db.refresh(tenant)
    new = {k: getattr(tenant, k, None) for k in old}
    audit_service.log_change(
        tenant_db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="tenants",
        record_id=tenant_id,
        old_values=old,
        new_values=new,
        ip_address=ip_address,
    )
    return tenant


def seed_default_departments(db: Session) -> None:
    if db.query(Department).count() > 0:
        return
    for name, dtype in DEFAULT_DEPARTMENTS:
        db.add(
            Department(
                department_id=str(uuid.uuid4()),
                department_name=name,
                department_type=dtype,
                is_active=True,
            )
        )
    db.commit()


def list_departments(db: Session) -> list[Department]:
    seed_default_departments(db)
    return db.query(Department).order_by(Department.department_name).all()


def create_department(db: Session, data: dict, actor_sub: str, ip: str | None = None) -> Department:
    row = Department(
        department_id=str(uuid.uuid4()),
        department_name=data["department_name"],
        department_type=data["department_type"],
        head_user_sub=data.get("head_user_sub"),
        is_active=data.get("is_active", True),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="CREATE",
        table_name="departments",
        record_id=str(row.department_id),
        new_values={"department_name": row.department_name, "department_type": row.department_type},
        ip_address=ip,
    )
    return row


def update_department(
    db: Session, department_id: uuid.UUID, data: dict, actor_sub: str, ip: str | None = None
) -> Department:
    row = db.query(Department).filter(Department.department_id == str(department_id)).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")
    old = {
        "department_name": row.department_name,
        "department_type": row.department_type,
        "head_user_sub": row.head_user_sub,
        "is_active": row.is_active,
    }
    for k, v in data.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="departments",
        record_id=str(department_id),
        old_values=old,
        new_values={k: getattr(row, k) for k in old},
        ip_address=ip,
    )
    return row


def delete_department(db: Session, department_id: uuid.UUID, actor_sub: str, ip: str | None = None) -> None:
    row = db.query(Department).filter(Department.department_id == str(department_id)).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")
    old = {"department_name": row.department_name}
    db.delete(row)
    db.commit()
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="DELETE",
        table_name="departments",
        record_id=str(department_id),
        old_values=old,
        ip_address=ip,
    )


def list_fee_schedules(
    db: Session,
    *,
    item_type: str | None = None,
    q: str | None = None,
    active_only: bool = False,
) -> list[FeeSchedule]:
    query = db.query(FeeSchedule)
    if item_type:
        query = query.filter(FeeSchedule.item_type == item_type)
    if active_only:
        today = date.today()
        query = query.filter(
            FeeSchedule.is_active.is_(True),
            FeeSchedule.effective_from <= today,
            or_(FeeSchedule.effective_to.is_(None), FeeSchedule.effective_to >= today),
        )
    if q:
        like = f"%{q}%"
        query = query.filter(or_(FeeSchedule.item_name.ilike(like), FeeSchedule.item_code.ilike(like)))
    return query.order_by(FeeSchedule.item_code).all()


def create_fee(db: Session, data: dict, actor_sub: str, ip: str | None = None) -> FeeSchedule:
    if db.query(FeeSchedule).filter(FeeSchedule.item_code == data["item_code"]).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="item_code already exists")
    row = FeeSchedule(
        fee_id=uuid.uuid4(),
        item_name=data["item_name"],
        item_code=data["item_code"],
        item_type=data["item_type"],
        standard_price=Decimal(str(data["standard_price"])),
        insurance_price=Decimal(str(data["insurance_price"])) if data.get("insurance_price") is not None else None,
        is_active=data.get("is_active", True),
        effective_from=data["effective_from"],
        effective_to=data.get("effective_to"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="CREATE",
        table_name="fee_schedules",
        record_id=str(row.fee_id),
        new_values={"item_code": row.item_code, "standard_price": str(row.standard_price)},
        ip_address=ip,
    )
    return row


def update_fee(db: Session, fee_id: uuid.UUID, data: dict, actor_sub: str, ip: str | None = None) -> FeeSchedule:
    row = db.query(FeeSchedule).filter(FeeSchedule.fee_id == fee_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Fee schedule not found")
    old = {"item_name": row.item_name, "standard_price": str(row.standard_price), "is_active": row.is_active}
    for k, v in data.items():
        if v is None:
            continue
        if k in ("standard_price", "insurance_price"):
            setattr(row, k, Decimal(str(v)))
        elif hasattr(row, k):
            setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="fee_schedules",
        record_id=str(fee_id),
        old_values=old,
        new_values={"item_name": row.item_name, "standard_price": str(row.standard_price), "is_active": row.is_active},
        ip_address=ip,
    )
    return row


def delete_fee(db: Session, fee_id: uuid.UUID, actor_sub: str, ip: str | None = None) -> None:
    row = db.query(FeeSchedule).filter(FeeSchedule.fee_id == fee_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Fee schedule not found")
    # Soft-deactivate preferred
    old = {"is_active": row.is_active}
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="DEACTIVATE",
        table_name="fee_schedules",
        record_id=str(fee_id),
        old_values=old,
        new_values={"is_active": False},
        ip_address=ip,
    )


def list_insurance_providers(db: Session) -> list[InsuranceProvider]:
    return db.query(InsuranceProvider).order_by(InsuranceProvider.name).all()


def create_insurance_provider(db: Session, data: dict, actor_sub: str, ip: str | None = None) -> InsuranceProvider:
    if db.query(InsuranceProvider).filter(InsuranceProvider.name == data["name"]).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Provider name already exists")
    row = InsuranceProvider(
        provider_id=uuid.uuid4(),
        name=data["name"],
        contact_email=data.get("contact_email"),
        contact_phone=data.get("contact_phone"),
        notes=data.get("notes"),
        is_active=data.get("is_active", True),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="CREATE",
        table_name="insurance_providers",
        record_id=str(row.provider_id),
        new_values={"name": row.name},
        ip_address=ip,
    )
    return row


def update_insurance_provider(
    db: Session, provider_id: uuid.UUID, data: dict, actor_sub: str, ip: str | None = None
) -> InsuranceProvider:
    row = db.query(InsuranceProvider).filter(InsuranceProvider.provider_id == provider_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Insurance provider not found")
    old = {"name": row.name, "is_active": row.is_active}
    for k, v in data.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="insurance_providers",
        record_id=str(provider_id),
        old_values=old,
        new_values={"name": row.name, "is_active": row.is_active},
        ip_address=ip,
    )
    return row


def delete_insurance_provider(db: Session, provider_id: uuid.UUID, actor_sub: str, ip: str | None = None) -> None:
    row = db.query(InsuranceProvider).filter(InsuranceProvider.provider_id == provider_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Insurance provider not found")
    old = {"name": row.name}
    db.delete(row)
    db.commit()
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="DELETE",
        table_name="insurance_providers",
        record_id=str(provider_id),
        old_values=old,
        ip_address=ip,
    )


def list_beds(db: Session) -> list[Bed]:
    return db.query(Bed).order_by(Bed.ward_name, Bed.bed_number).all()


def list_wards(db: Session) -> list[dict]:
    """Distinct ward names with bed counts (wards are not a separate table)."""
    beds = db.query(Bed).filter(Bed.is_active.is_(True)).order_by(Bed.ward_name).all()
    by_ward: dict[str, dict] = {}
    for b in beds:
        entry = by_ward.setdefault(
            b.ward_name, {"ward_name": b.ward_name, "bed_count": 0, "available": 0}
        )
        entry["bed_count"] += 1
        if b.is_available:
            entry["available"] += 1
    return list(by_ward.values())


def beds_summary(db: Session) -> dict[str, int]:
    total = db.query(func.count(Bed.bed_id)).filter(Bed.is_active.is_(True)).scalar() or 0
    available = (
        db.query(func.count(Bed.bed_id))
        .filter(Bed.is_active.is_(True), Bed.is_available.is_(True))
        .scalar()
        or 0
    )
    return {"total": int(total), "available": int(available), "occupied": int(total) - int(available)}


def create_bed(db: Session, data: dict, actor_sub: str, ip: str | None = None) -> Bed:
    existing = (
        db.query(Bed)
        .filter(Bed.ward_name == data["ward_name"], Bed.bed_number == data["bed_number"])
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Bed already exists in ward")
    row = Bed(
        bed_id=uuid.uuid4(),
        ward_name=data["ward_name"],
        bed_number=data["bed_number"],
        bed_type=data.get("bed_type", "general"),
        is_available=data.get("is_available", True),
        is_active=data.get("is_active", True),
        notes=data.get("notes"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="CREATE",
        table_name="beds",
        record_id=str(row.bed_id),
        new_values={"ward_name": row.ward_name, "bed_number": row.bed_number},
        ip_address=ip,
    )
    return row


def update_bed(db: Session, bed_id: uuid.UUID, data: dict, actor_sub: str, ip: str | None = None) -> Bed:
    row = db.query(Bed).filter(Bed.bed_id == bed_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bed not found")
    old = {
        "ward_name": row.ward_name,
        "bed_number": row.bed_number,
        "is_available": row.is_available,
        "is_active": row.is_active,
    }
    for k, v in data.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="UPDATE",
        table_name="beds",
        record_id=str(bed_id),
        old_values=old,
        new_values={k: getattr(row, k) for k in old},
        ip_address=ip,
    )
    return row


def delete_bed(db: Session, bed_id: uuid.UUID, actor_sub: str, ip: str | None = None) -> None:
    row = db.query(Bed).filter(Bed.bed_id == bed_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bed not found")
    old = {"ward_name": row.ward_name, "bed_number": row.bed_number}
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    audit_service.log_change(
        db,
        user_id=actor_sub,
        action="DEACTIVATE",
        table_name="beds",
        record_id=str(bed_id),
        old_values=old,
        new_values={"is_active": False},
        ip_address=ip,
    )


# ---- Keycloak role guards (FR-54) ----


async def guarded_delete_realm_role(realm: str, role_name: str) -> None:
    if role_name in SYSTEM_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"System role '{role_name}' cannot be deleted",
        )
    await delete_realm_role(realm, role_name)


async def guarded_update_realm_role(realm: str, role_name: str, new_name: str) -> None:
    if role_name in SYSTEM_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"System role '{role_name}' cannot be renamed",
        )
    await update_realm_role(realm, role_name, new_name)


async def sync_tenant_role_to_keycloak(realm: str, role_name: str) -> None:
    await ensure_roles([role_name], realm=realm)
    try:
        await create_realm_role(realm, role_name)
    except Exception as e:
        if "already exists" not in str(e).lower():
            # ensure_roles may have created it; ignore conflict
            if "409" not in str(e) and "Conflict" not in str(e):
                logger.warning("sync_tenant_role_to_keycloak: %s", e)
