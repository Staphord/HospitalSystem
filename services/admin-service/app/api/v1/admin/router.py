import csv
import io
import logging
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_role
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.limiter import limiter
from app.dependencies import get_tenant_db_for_request
from app.services import admin as admin_svc
from app.services import audit_service
from app.services import backup as backup_svc
from app.services import login_history as login_history_svc
from app.services import reports as reports_svc
from app.services import sessions as sessions_svc
from app.services import settings_service
from app.services.keycloak_admin import (
    create_realm_role,
    get_realm_roles,
)
from app.services.roles import SYSTEM_ROLES
from app.events.publisher import publish_user_created, publish_user_deactivated
from app.models.master import Tenant, TenantRole as TenantRoleModel

from app.api.v1.admin.schemas import (
    ActiveSessionOut,
    AuditLogListOut,
    AuditLogOut,
    BackupJobOut,
    BedCreate,
    BedOut,
    BedUpdate,
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    FeeScheduleCreate,
    FeeScheduleOut,
    FeeScheduleUpdate,
    GlobalRoleOut,
    HospitalProfileOut,
    HospitalProfileUpdate,
    HospitalSettingOut,
    HospitalSettingsUpdate,
    HospitalUserCreate,
    HospitalUserOut,
    HospitalUserUpdate,
    InsuranceProviderCreate,
    InsuranceProviderOut,
    InsuranceProviderUpdate,
    LoginHistoryOut,
    PermissionOut,
    PermissionUpdate,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    TenantRoleCreate,
    TenantRoleOut,
    TenantRoleUpdate,
    WardOut,
)

logger = logging.getLogger("admin_service.admin")

router = APIRouter(dependencies=[Depends(require_role("hospital_admin"))])


def _get_tenant_realm(db: Session, tenant_id: str) -> str:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if tenant and tenant.keycloak_realm:
        return tenant.keycloak_realm
    from app.config import settings

    return settings.keycloak_realm


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_out(u) -> HospitalUserOut:
    return HospitalUserOut.model_validate(u)


# ---------------------------------------------------------------------------
# Users (FR-53)
# ---------------------------------------------------------------------------


@router.get("/users/assignable-roles", response_model=list[str], tags=["Users"])
@limiter.limit("60/minute")
async def assignable_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[str]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    return await admin_svc.list_assignable_roles(master_db, ctx.tenant_id, realm)


@router.get("/users", response_model=list[HospitalUserOut], tags=["Users"])
@limiter.limit("30/minute")
async def list_hospital_users(
    request: Request,
    response: Response,
    role: str | None = None,
    is_active: bool | None = None,
    department_id: UUID | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("username"),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[HospitalUserOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    users, total = admin_svc.list_users(
        db,
        ctx.tenant_id,
        role=role,
        is_active=is_active,
        department_id=department_id,
        q=q,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    response.headers["X-Total-Count"] = str(total)
    return [_user_out(u) for u in users]


@router.get("/users/{sub}", response_model=HospitalUserOut, tags=["Users"])
@limiter.limit("60/minute")
async def get_hospital_user(
    request: Request,
    sub: str,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalUserOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    return _user_out(admin_svc.get_user(db, ctx.tenant_id, sub))


@router.post("/users", response_model=HospitalUserOut, status_code=201, tags=["Users"])
@limiter.limit("30/minute")
async def create_hospital_user(
    request: Request,
    body: HospitalUserCreate,
    db: Session = Depends(get_tenant_db_for_request),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalUserOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    user = await admin_svc.create_user(
        db,
        master_db,
        tenant_id=ctx.tenant_id,
        realm=realm,
        username=body.username,
        password=body.password,
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        actor_sub=ctx.user_sub,
        department_id=body.department_id,
        phone=body.phone,
        ip_address=_client_ip(request),
    )
    try:
        await publish_user_created(
            ctx.tenant_id,
            user.keycloak_sub,
            {"username": user.username, "role": user.role, "email": user.email},
        )
    except Exception:
        logger.exception("Failed to publish user.created")
    return _user_out(user)


@router.patch("/users/{sub}", response_model=HospitalUserOut, tags=["Users"])
@limiter.limit("30/minute")
async def update_hospital_user(
    request: Request,
    sub: str,
    body: HospitalUserUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalUserOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    was_active = True
    try:
        existing = admin_svc.get_user(db, ctx.tenant_id, sub)
        was_active = existing.is_active
    except HTTPException:
        pass

    updated = await admin_svc.update_user(
        db,
        tenant_id=ctx.tenant_id,
        realm=realm,
        sub=sub,
        actor_sub=ctx.user_sub,
        username=body.username,
        email=str(body.email) if body.email else None,
        full_name=body.full_name,
        password=body.password,
        role=body.role,
        is_active=body.is_active,
        force_password_change=body.force_password_change,
        department_id=body.department_id,
        phone=body.phone,
        reason=body.reason,
        ip_address=_client_ip(request),
    )
    if was_active and body.is_active is False:
        try:
            await publish_user_deactivated(
                ctx.tenant_id,
                sub,
                {"reason": body.reason, "by": ctx.user_sub},
            )
        except Exception:
            logger.exception("Failed to publish user.deactivated")
    return _user_out(updated)


@router.delete("/users/{sub}", status_code=204, tags=["Users"])
@limiter.limit("30/minute")
async def delete_hospital_user(
    request: Request,
    sub: str,
    hard: bool = Query(False, description="Hard-delete from Keycloak and DB"),
    db: Session = Depends(get_tenant_db_for_request),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    await admin_svc.delete_user(
        db,
        tenant_id=ctx.tenant_id,
        realm=realm,
        sub=sub,
        actor_sub=ctx.user_sub,
        hard=hard,
        ip_address=_client_ip(request),
    )
    try:
        await publish_user_deactivated(ctx.tenant_id, sub, {"hard": hard, "by": ctx.user_sub})
    except Exception:
        logger.exception("Failed to publish user.deactivated on delete")


# ---------------------------------------------------------------------------
# Keycloak realm roles
# ---------------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleOut], tags=["Roles"])
@limiter.limit("60/minute")
async def list_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[RoleOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        roles = await get_realm_roles(realm)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to list roles: {e}") from e
    return [RoleOut.model_validate(r) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=201, tags=["Roles"])
@limiter.limit("30/minute")
async def create_role(
    request: Request,
    body: RoleCreate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> RoleOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        role = await create_realm_role(realm, body.name)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to create role: {e}") from e
    return RoleOut.model_validate(role)


@router.put("/roles/{role_name}", response_model=dict, tags=["Roles"])
@limiter.limit("30/minute")
async def update_role(
    request: Request,
    role_name: str,
    body: RoleUpdate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        await admin_svc.guarded_update_realm_role(realm, role_name, body.name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to update role: {e}") from e
    return {"detail": f"Role '{role_name}' renamed to '{body.name}'"}


@router.delete("/roles/{role_name}", status_code=204, tags=["Roles"])
@limiter.limit("30/minute")
async def delete_role(
    request: Request,
    role_name: str,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        await admin_svc.guarded_delete_realm_role(realm, role_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Failed to delete role: {e}") from e


@router.get("/global-roles", response_model=list[GlobalRoleOut], tags=["Global Roles"])
@limiter.limit("60/minute")
async def list_global_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[GlobalRoleOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    result = master_db.execute(
        text(
            "SELECT global_role_id, name, description, scope, created_at, updated_at "
            "FROM global_roles ORDER BY created_at DESC"
        )
    )
    rows = result.fetchall()
    return [
        GlobalRoleOut(
            global_role_id=row[0],
            name=row[1],
            description=row[2],
            scope=row[3],
            created_at=row[4],
            updated_at=row[5],
        )
        for row in rows
    ]


@router.post("/tenant-roles", response_model=TenantRoleOut, status_code=201, tags=["Tenant Roles"])
@limiter.limit("30/minute")
async def create_tenant_role(
    request: Request,
    body: TenantRoleCreate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> TenantRoleOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    existing_global = master_db.execute(
        text("SELECT 1 FROM global_roles WHERE name = :name"),
        {"name": body.name},
    ).scalar()
    if existing_global:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Role '{body.name}' already exists as a global role.",
        )
    existing = (
        master_db.query(TenantRoleModel)
        .filter(TenantRoleModel.tenant_id == ctx.tenant_id, TenantRoleModel.name == body.name)
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Role '{body.name}' already exists in this tenant")

    role = TenantRoleModel(
        tenant_id=ctx.tenant_id,
        name=body.name,
        description=body.description,
        scope=body.scope,
        created_by=ctx.user_sub,
    )
    master_db.add(role)
    master_db.commit()
    master_db.refresh(role)

    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    await admin_svc.sync_tenant_role_to_keycloak(realm, body.name)
    return TenantRoleOut.model_validate(role)


@router.get("/tenant-roles", response_model=list[TenantRoleOut], tags=["Tenant Roles"])
@limiter.limit("60/minute")
async def list_tenant_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[TenantRoleOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    roles = (
        master_db.query(TenantRoleModel)
        .filter(TenantRoleModel.tenant_id == ctx.tenant_id)
        .order_by(TenantRoleModel.created_at.desc())
        .all()
    )
    return [TenantRoleOut.model_validate(r) for r in roles]


@router.patch("/tenant-roles/{role_id}", response_model=TenantRoleOut, tags=["Tenant Roles"])
@limiter.limit("30/minute")
async def update_tenant_role(
    request: Request,
    role_id: UUID,
    body: TenantRoleUpdate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> TenantRoleOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    role = (
        master_db.query(TenantRoleModel)
        .filter(TenantRoleModel.tenant_role_id == role_id, TenantRoleModel.tenant_id == ctx.tenant_id)
        .first()
    )
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant role not found")
    old_name = role.name
    if body.name is not None:
        if body.name in SYSTEM_ROLES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot rename to a system role name conflict")
        existing = (
            master_db.query(TenantRoleModel)
            .filter(
                TenantRoleModel.tenant_id == ctx.tenant_id,
                TenantRoleModel.name == body.name,
                TenantRoleModel.tenant_role_id != role_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Role '{body.name}' already exists")
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.scope is not None:
        role.scope = body.scope
    master_db.commit()
    master_db.refresh(role)
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    if body.name and body.name != old_name:
        try:
            await admin_svc.guarded_update_realm_role(realm, old_name, body.name)
        except HTTPException:
            await admin_svc.sync_tenant_role_to_keycloak(realm, body.name)
    return TenantRoleOut.model_validate(role)


@router.delete("/tenant-roles/{role_id}", status_code=204, tags=["Tenant Roles"])
@limiter.limit("30/minute")
async def delete_tenant_role(
    request: Request,
    role_id: UUID,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    role = (
        master_db.query(TenantRoleModel)
        .filter(TenantRoleModel.tenant_role_id == role_id, TenantRoleModel.tenant_id == ctx.tenant_id)
        .first()
    )
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant role not found")
    name = role.name
    master_db.delete(role)
    master_db.commit()
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    if name not in SYSTEM_ROLES:
        try:
            await admin_svc.guarded_delete_realm_role(realm, name)
        except Exception:
            logger.exception("Failed deleting Keycloak role %s", name)


# ---------------------------------------------------------------------------
# Permissions (FR-54)
# ---------------------------------------------------------------------------


@router.get("/permissions", response_model=list[PermissionOut], tags=["Permissions"])
@limiter.limit("60/minute")
async def get_permissions(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[PermissionOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    return [PermissionOut.model_validate(p) for p in admin_svc.list_permissions(db)]


@router.put("/permissions/{role_name}", response_model=PermissionOut, tags=["Permissions"])
@limiter.limit("30/minute")
async def put_permission(
    request: Request,
    role_name: str,
    body: PermissionUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> PermissionOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    row = admin_svc.upsert_permission(
        db,
        role_name,
        body.modules,
        body.actions,
        ctx.user_sub,
        ip_address=_client_ip(request),
    )
    return PermissionOut.model_validate(row)


# ---------------------------------------------------------------------------
# Audit logs (FR-56)
# ---------------------------------------------------------------------------


@router.get("/audit-logs", response_model=AuditLogListOut, tags=["Audit"])
@limiter.limit("60/minute")
async def list_audit_logs(
    request: Request,
    user_id: str | None = None,
    action: str | None = None,
    table_name: str | None = None,
    from_dt: datetime | None = Query(None, alias="from"),
    to_dt: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AuditLogListOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    rows, total = audit_service.list_audit_logs(
        db,
        user_id=user_id,
        action=action,
        table_name=table_name,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
        offset=offset,
    )
    return AuditLogListOut(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/audit-logs/export", tags=["Audit"])
@limiter.limit("10/minute")
async def export_audit_logs(
    request: Request,
    format: str = Query("csv", pattern="^(csv|json)$"),
    user_id: str | None = None,
    action: str | None = None,
    table_name: str | None = None,
    from_dt: datetime | None = Query(None, alias="from"),
    to_dt: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    rows, _ = audit_service.list_audit_logs(
        db,
        user_id=user_id,
        action=action,
        table_name=table_name,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=5000,
        offset=0,
    )
    if format == "json":
        import json

        payload = [AuditLogOut.model_validate(r).model_dump(mode="json") for r in rows]
        return Response(
            content=json.dumps(payload, default=str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["log_id", "user_id", "action", "table_name", "record_id", "ip_address", "created_at"]
    )
    for r in rows:
        writer.writerow(
            [r.log_id, r.user_id, r.action, r.table_name, r.record_id, r.ip_address, r.created_at]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get("/audit-logs/{log_id}", response_model=AuditLogOut, tags=["Audit"])
@limiter.limit("60/minute")
async def get_audit_log(
    request: Request,
    log_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AuditLogOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    row = audit_service.get_audit_log(db, log_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Audit log not found")
    return AuditLogOut.model_validate(row)


# ---------------------------------------------------------------------------
# Hospital profile / departments / fees / insurance / beds (FR-55)
# ---------------------------------------------------------------------------


@router.get("/hospital-profile", response_model=HospitalProfileOut, tags=["Hospital Profile"])
@limiter.limit("60/minute")
async def get_profile(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalProfileOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    return HospitalProfileOut.model_validate(admin_svc.get_hospital_profile(master_db, ctx.tenant_id))


@router.patch("/hospital-profile", response_model=HospitalProfileOut, tags=["Hospital Profile"])
@limiter.limit("30/minute")
async def patch_profile(
    request: Request,
    body: HospitalProfileUpdate,
    master_db: Session = Depends(get_db),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalProfileOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    tenant = admin_svc.update_hospital_profile(
        master_db,
        db,
        ctx.tenant_id,
        ctx.user_sub,
        body.model_dump(exclude_unset=True),
        ip_address=_client_ip(request),
    )
    return HospitalProfileOut.model_validate(tenant)


@router.get("/departments", response_model=list[DepartmentOut], tags=["Departments"])
@limiter.limit("60/minute")
async def get_departments(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[DepartmentOut]:
    return [DepartmentOut.model_validate(d) for d in admin_svc.list_departments(db)]


@router.post("/departments", response_model=DepartmentOut, status_code=201, tags=["Departments"])
@limiter.limit("30/minute")
async def post_department(
    request: Request,
    body: DepartmentCreate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> DepartmentOut:
    row = admin_svc.create_department(
        db, body.model_dump(), ctx.user_sub, ip=_client_ip(request)
    )
    return DepartmentOut.model_validate(row)


@router.patch("/departments/{department_id}", response_model=DepartmentOut, tags=["Departments"])
@limiter.limit("30/minute")
async def patch_department(
    request: Request,
    department_id: UUID,
    body: DepartmentUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> DepartmentOut:
    row = admin_svc.update_department(
        db, department_id, body.model_dump(exclude_unset=True), ctx.user_sub, ip=_client_ip(request)
    )
    return DepartmentOut.model_validate(row)


@router.delete("/departments/{department_id}", status_code=204, tags=["Departments"])
@limiter.limit("30/minute")
async def remove_department(
    request: Request,
    department_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    admin_svc.delete_department(db, department_id, ctx.user_sub, ip=_client_ip(request))


@router.get("/fee-schedules", response_model=list[FeeScheduleOut], tags=["Fee Schedules"])
@limiter.limit("60/minute")
async def get_fees(
    request: Request,
    item_type: str | None = None,
    q: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[FeeScheduleOut]:
    return [
        FeeScheduleOut.model_validate(f)
        for f in admin_svc.list_fee_schedules(db, item_type=item_type, q=q, active_only=active_only)
    ]


@router.post("/fee-schedules", response_model=FeeScheduleOut, status_code=201, tags=["Fee Schedules"])
@limiter.limit("30/minute")
async def post_fee(
    request: Request,
    body: FeeScheduleCreate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> FeeScheduleOut:
    row = admin_svc.create_fee(db, body.model_dump(), ctx.user_sub, ip=_client_ip(request))
    return FeeScheduleOut.model_validate(row)


@router.patch("/fee-schedules/{fee_id}", response_model=FeeScheduleOut, tags=["Fee Schedules"])
@limiter.limit("30/minute")
async def patch_fee(
    request: Request,
    fee_id: UUID,
    body: FeeScheduleUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> FeeScheduleOut:
    row = admin_svc.update_fee(
        db, fee_id, body.model_dump(exclude_unset=True), ctx.user_sub, ip=_client_ip(request)
    )
    return FeeScheduleOut.model_validate(row)


@router.delete("/fee-schedules/{fee_id}", status_code=204, tags=["Fee Schedules"])
@limiter.limit("30/minute")
async def remove_fee(
    request: Request,
    fee_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    admin_svc.delete_fee(db, fee_id, ctx.user_sub, ip=_client_ip(request))


@router.get("/insurance-providers", response_model=list[InsuranceProviderOut], tags=["Insurance Providers"])
@limiter.limit("60/minute")
async def get_providers(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[InsuranceProviderOut]:
    return [InsuranceProviderOut.model_validate(p) for p in admin_svc.list_insurance_providers(db)]


@router.post(
    "/insurance-providers",
    response_model=InsuranceProviderOut,
    status_code=201,
    tags=["Insurance Providers"],
)
@limiter.limit("30/minute")
async def post_provider(
    request: Request,
    body: InsuranceProviderCreate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> InsuranceProviderOut:
    row = admin_svc.create_insurance_provider(
        db, body.model_dump(), ctx.user_sub, ip=_client_ip(request)
    )
    return InsuranceProviderOut.model_validate(row)


@router.patch(
    "/insurance-providers/{provider_id}",
    response_model=InsuranceProviderOut,
    tags=["Insurance Providers"],
)
@limiter.limit("30/minute")
async def patch_provider(
    request: Request,
    provider_id: UUID,
    body: InsuranceProviderUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> InsuranceProviderOut:
    row = admin_svc.update_insurance_provider(
        db, provider_id, body.model_dump(exclude_unset=True), ctx.user_sub, ip=_client_ip(request)
    )
    return InsuranceProviderOut.model_validate(row)


@router.delete("/insurance-providers/{provider_id}", status_code=204, tags=["Insurance Providers"])
@limiter.limit("30/minute")
async def remove_provider(
    request: Request,
    provider_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    admin_svc.delete_insurance_provider(db, provider_id, ctx.user_sub, ip=_client_ip(request))


# ── Wards & beds (create beds here; ward-service assigns/releases them) ─────

@router.get(
    "/wards",
    response_model=list[WardOut],
    tags=["Wards & Beds"],
    summary="List wards",
    description=(
        "Returns distinct ward names derived from active beds "
        "(there is no separate wards table). Create beds with POST /beds."
    ),
)
@limiter.limit("60/minute")
async def get_wards(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[WardOut]:
    return [WardOut.model_validate(w) for w in admin_svc.list_wards(db)]


@router.get(
    "/beds/summary",
    tags=["Wards & Beds"],
    summary="Bed occupancy summary",
)
@limiter.limit("60/minute")
async def get_beds_summary(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return admin_svc.beds_summary(db)


@router.get(
    "/beds",
    response_model=list[BedOut],
    tags=["Wards & Beds"],
    summary="List all beds",
)
@limiter.limit("60/minute")
async def get_beds(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[BedOut]:
    return [BedOut.model_validate(b) for b in admin_svc.list_beds(db)]


@router.post(
    "/beds",
    response_model=BedOut,
    status_code=201,
    tags=["Wards & Beds"],
    summary="Create bed (defines the ward)",
    description=(
        "Creates a bed under a ward_name. Use the same ward_name for all beds in that ward "
        "(e.g. 'Medical Ward A', 'ICU'). After beds exist, use ward-service to assign patients."
    ),
)
@limiter.limit("30/minute")
async def post_bed(
    request: Request,
    body: BedCreate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> BedOut:
    row = admin_svc.create_bed(db, body.model_dump(), ctx.user_sub, ip=_client_ip(request))
    return BedOut.model_validate(row)


@router.patch(
    "/beds/{bed_id}",
    response_model=BedOut,
    tags=["Wards & Beds"],
    summary="Update bed",
)
@limiter.limit("30/minute")
async def patch_bed(
    request: Request,
    bed_id: UUID,
    body: BedUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> BedOut:
    row = admin_svc.update_bed(
        db, bed_id, body.model_dump(exclude_unset=True), ctx.user_sub, ip=_client_ip(request)
    )
    return BedOut.model_validate(row)


@router.delete(
    "/beds/{bed_id}",
    status_code=204,
    tags=["Wards & Beds"],
    summary="Deactivate bed",
)
@limiter.limit("30/minute")
async def remove_bed(
    request: Request,
    bed_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    admin_svc.delete_bed(db, bed_id, ctx.user_sub, ip=_client_ip(request))


# ---------------------------------------------------------------------------
# Reports (FR-57)
# ---------------------------------------------------------------------------


@router.get("/reports/patient-census", tags=["Reports"])
@limiter.limit("30/minute")
async def report_census(
    request: Request,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.patient_census(db, from_date, to_date)


@router.get("/reports/wait-times", tags=["Reports"])
@limiter.limit("30/minute")
async def report_wait_times(
    request: Request,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.wait_times(db, from_date, to_date)


@router.get("/reports/discharges", tags=["Reports"])
@limiter.limit("30/minute")
async def report_discharges(
    request: Request,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.discharges(db, from_date, to_date)


@router.get("/reports/bed-occupancy", tags=["Reports"])
@limiter.limit("30/minute")
async def report_bed_occupancy(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.bed_occupancy(db)


@router.get("/reports/revenue-summary", tags=["Reports"])
@limiter.limit("30/minute")
async def report_revenue(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.revenue_summary()


@router.get("/reports/dashboard", tags=["Reports"])
@limiter.limit("60/minute")
async def report_dashboard(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    return reports_svc.dashboard(db)


# ---------------------------------------------------------------------------
# Backups (FR-58)
# ---------------------------------------------------------------------------


@router.post("/backups", response_model=BackupJobOut, status_code=201, tags=["Backups"])
@limiter.limit("5/minute")
async def create_backup(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> BackupJobOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    job = backup_svc.create_backup_job(
        db,
        tenant_id=ctx.tenant_id,
        triggered_by="user",
        triggered_by_sub=ctx.user_sub,
    )
    job = backup_svc.run_backup_job(db, job)
    return BackupJobOut.model_validate(job)


@router.get("/backups", response_model=list[BackupJobOut], tags=["Backups"])
@limiter.limit("30/minute")
async def list_backup_jobs(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[BackupJobOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    return [BackupJobOut.model_validate(j) for j in backup_svc.list_backups(db, ctx.tenant_id)]


@router.get("/backups/status", tags=["Backups"])
@limiter.limit("60/minute")
async def get_backup_status(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    return backup_svc.backup_status(db, ctx.tenant_id)


@router.get("/backups/{backup_id}/download", tags=["Backups"])
@limiter.limit("10/minute")
async def download_backup(
    request: Request,
    backup_id: UUID,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
):
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    job = backup_svc.get_backup(db, ctx.tenant_id, backup_id)
    path = backup_svc.resolve_download_path(job)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/sql",
    )


# ---------------------------------------------------------------------------
# Active sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[ActiveSessionOut], tags=["Sessions"])
@limiter.limit("30/minute")
async def list_sessions(
    request: Request,
    master_db: Session = Depends(get_db),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[ActiveSessionOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    rows = sessions_svc.list_active_sessions(master_db, db, ctx.tenant_id)
    return [ActiveSessionOut.model_validate(r) for r in rows]


@router.delete("/sessions/{session_id}", status_code=204, tags=["Sessions"])
@limiter.limit("30/minute")
async def revoke_session(
    request: Request,
    session_id: str,
    master_db: Session = Depends(get_db),
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    await sessions_svc.revoke_session(
        master_db,
        db,
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        actor_sub=ctx.user_sub,
        realm=realm,
        ip=_client_ip(request),
    )


# ---------------------------------------------------------------------------
# Hospital settings (KV extras beyond hospital-profile)
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=list[HospitalSettingOut], tags=["Settings"])
@limiter.limit("60/minute")
async def get_settings(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[HospitalSettingOut]:
    return [HospitalSettingOut.model_validate(s) for s in settings_service.list_settings(db)]


@router.put("/settings", response_model=list[HospitalSettingOut], tags=["Settings"])
@limiter.limit("30/minute")
async def put_settings(
    request: Request,
    body: HospitalSettingsUpdate,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[HospitalSettingOut]:
    rows = settings_service.upsert_settings(
        db,
        body.settings,
        actor_sub=ctx.user_sub,
        ip=_client_ip(request),
    )
    return [HospitalSettingOut.model_validate(s) for s in rows]


# ---------------------------------------------------------------------------
# Staff login history
# ---------------------------------------------------------------------------


@router.get(
    "/users/{sub}/login-history",
    response_model=list[LoginHistoryOut],
    tags=["Users"],
)
@limiter.limit("30/minute")
async def get_user_login_history(
    request: Request,
    sub: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[LoginHistoryOut]:
    rows = login_history_svc.list_login_history(
        master_db,
        user_sub=sub,
        tenant_id=ctx.tenant_id,
        days=days,
        limit=limit,
    )
    return [LoginHistoryOut.model_validate(r) for r in rows]
