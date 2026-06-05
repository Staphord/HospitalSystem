from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.limiter import limiter
from app.services.keycloak_admin import (
    create_keycloak_user,
    delete_keycloak_user,
    ensure_roles,
    create_local_user,
    update_local_user,
    delete_local_user,
)
from app.api.v1.superadmin.schemas import UserCreate, UserUpdate, UserOut, UserDelete, TenantOut, TenantCreate, TenantUpdate, RoleCreate
from app.models.user import User
from app.models.master import Tenant

router = APIRouter(dependencies=[Depends(require_role("super_admin"))])


@router.post("/users", response_model=UserOut, status_code=201)
@limiter.limit("30/minute")
async def create_user(
    request: Request,
    body: UserCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> UserOut:
    if body.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin can only create other super_admin users. "
                   "Use /admin/users for hospital-level user creation.",
        )

    await ensure_roles(["super_admin", "hospital_admin"])

    kc_roles = ["super_admin", "hospital_admin"]

    try:
        kc_sub = await create_keycloak_user(
            username=body.username,
            password=body.password,
            email=body.email,
            roles=kc_roles,
            full_name=body.full_name or None,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create user in Keycloak: {e}",
        )

    if body.hospital_id:
        from app.services.keycloak_admin import set_user_attribute
        await set_user_attribute(kc_sub, "tenant_id", body.hospital_id)

    user = create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=body.username,
        full_name=body.full_name or None,
        email=body.email,
        role=body.role,
        hospital_id=body.hospital_id,
    )

    return UserOut(
        keycloak_sub=user.keycloak_sub,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        hospital_id=user.hospital_id,
    )


@router.patch("/users/{sub}", response_model=UserOut)
@limiter.limit("30/minute")
async def update_user(
    request: Request,
    sub: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> UserOut:
    user = db.query(User).filter(User.keycloak_sub == sub).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    from app.services.keycloak_admin import update_keycloak_user, set_user_attribute, set_user_password
    if body.username is not None or body.email is not None or (body.full_name is not None and body.full_name.strip()):
        await update_keycloak_user(
            user.keycloak_sub,
            username=body.username,
            email=body.email,
            full_name=body.full_name,
        )
    if body.password is not None:
        await set_user_password(user.keycloak_sub, body.password)
    if body.hospital_id is not None:
        await set_user_attribute(user.keycloak_sub, "tenant_id", body.hospital_id)

    updated = update_local_user(
        db=db,
        keycloak_sub=sub,
        username=body.username,
        full_name=body.full_name,
        email=body.email,
        role=body.role,
        hospital_id=body.hospital_id,
    )
    return UserOut(
        keycloak_sub=updated.keycloak_sub,
        username=updated.username,
        full_name=updated.full_name,
        email=updated.email,
        role=updated.role,
        hospital_id=updated.hospital_id,
    )


@router.delete("/users", status_code=204)
@limiter.limit("30/minute")
async def delete_user(
    request: Request,
    body: UserDelete,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    kc_sub = await delete_keycloak_user(body.username)
    if not kc_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{body.username}' not found in Keycloak",
        )
    delete_local_user(db, kc_sub)


@router.get("/users", response_model=list[UserOut])
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[UserOut]:
    users = db.query(User).filter(User.role == "super_admin").all()
    return [
        UserOut(
            keycloak_sub=u.keycloak_sub,
            username=u.username,
            full_name=u.full_name,
            email=u.email,
            role=u.role,
            hospital_id=u.hospital_id,
        )
        for u in users
    ]


@router.get("/tenants", response_model=list[TenantOut])
@limiter.limit("30/minute")
async def list_tenants(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[TenantOut]:
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return [TenantOut.model_validate(t) for t in tenants]


@router.post("/tenants", response_model=TenantOut, status_code=201)
@limiter.limit("10/minute")
async def create_tenant(
    request: Request,
    body: TenantCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> TenantOut:
    import uuid
    from datetime import datetime, timezone
    from app.services.tenant_service import encrypt_dsn

    tenant_id = f"hosp-{uuid.uuid4().hex[:8]}"
    existing = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Tenant ID collision")

    tenant = Tenant(
        tenant_id=tenant_id,
        name=body.hospital_name,
        db_dsn_encrypted=encrypt_dsn(f"postgresql://placeholder@{tenant_id}:5432/{tenant_id}"),
        status="active",
        subscription_plan=body.subscription_plan,
        subscription_start=datetime.now(timezone.utc),
        subscription_end=datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1),
        is_active=True,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    await ensure_roles(["hospital_user", "hospital_admin"])
    try:
        kc_sub = await create_keycloak_user(
            username=body.admin_username,
            password=body.admin_password,
            email=body.admin_email,
            roles=["hospital_admin", "hospital_user"],
            full_name=body.admin_full_name or None,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create admin user: {e}",
        )

    from app.services.keycloak_admin import set_user_attribute
    await set_user_attribute(kc_sub, "tenant_id", tenant_id)

    create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=body.admin_username,
        full_name=body.admin_full_name or None,
        email=body.admin_email,
        role="hospital_admin",
        hospital_id=tenant_id,
    )

    return TenantOut.model_validate(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
@limiter.limit("30/minute")
async def update_tenant(
    request: Request,
    tenant_id: str,
    body: TenantUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> TenantOut:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if body.name is not None:
        tenant.name = body.name
    if body.status is not None:
        tenant.status = body.status
    if body.subscription_plan is not None:
        tenant.subscription_plan = body.subscription_plan
    if body.is_active is not None:
        tenant.is_active = body.is_active
        if not body.is_active:
            tenant.status = "suspended"
        elif body.is_active and tenant.status == "suspended":
            tenant.status = "active"

    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.post("/roles", status_code=201)
@limiter.limit("30/minute")
async def create_role(
    request: Request,
    body: RoleCreate,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    await ensure_roles([body.name])
    return {"detail": f"Role '{body.name}' ensured"}
