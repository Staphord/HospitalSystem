import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.limiter import limiter
from app.dependencies import get_tenant_db_for_request
from app.services.keycloak_admin import (
    assign_user_roles,
    create_keycloak_user,
    create_local_user,
    create_realm_role,
    delete_keycloak_user,
    delete_local_user,
    delete_realm_role,
    ensure_roles,
    get_local_users_by_hospital,
    get_realm_roles,
    set_user_attribute,
    set_user_password,
    update_keycloak_user,
    update_local_user,
    update_realm_role,
)
from uuid import UUID

from app.api.v1.admin.schemas import (
    GlobalRoleOut,
    HospitalUserCreate,
    HospitalUserUpdate,
    HospitalUserOut,
    RoleCreate,
    RoleUpdate,
    RoleOut,
    TenantRoleCreate,
    TenantRoleUpdate,
    TenantRoleOut,
)
from app.models.user import User
from app.models.master import Tenant, TenantRole as TenantRoleModel

logger = logging.getLogger("admin_service.admin")

router = APIRouter(dependencies=[Depends(require_role("hospital_admin"))])


def _get_tenant_realm(db: Session, tenant_id: str) -> str:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if tenant and tenant.keycloak_realm:
        return tenant.keycloak_realm
    from app.config import settings
    return settings.keycloak_realm


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[HospitalUserOut], tags=["Users"])
@limiter.limit("30/minute")
async def list_hospital_users(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[HospitalUserOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    users = get_local_users_by_hospital(db=db, hospital_id=ctx.tenant_id)
    return [
        HospitalUserOut(
            keycloak_sub=u.keycloak_sub,
            username=u.username,
            full_name=u.full_name,
            email=u.email,
            role=u.role,
            hospital_id=u.hospital_id,
            is_active=u.is_active,
            force_password_change=u.force_password_change,
        )
        for u in users
    ]


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

    predefined_roles = {"hospital_admin", "hospital_user", "nurse", "clinician", "doctor", "patient"}
    roles_to_ensure = list(predefined_roles | {body.role})
    await ensure_roles(roles_to_ensure, realm=realm)

    kc_roles_map = {
        "hospital_admin": ["hospital_admin", "hospital_user"],
        "hospital_user": ["hospital_user"],
        "nurse": ["nurse", "hospital_user"],
        "clinician": ["clinician", "hospital_user"],
        "doctor": ["doctor", "hospital_user"],
        "patient": ["patient", "hospital_user"],
    }
    kc_roles = kc_roles_map.get(body.role, [body.role, "hospital_user"])

    try:
        kc_sub = await create_keycloak_user(
            username=body.username,
            password=body.password,
            email=body.email,
            roles=kc_roles,
            full_name=body.full_name or None,
            realm=realm,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create user in Keycloak: {e}",
        )

    await set_user_attribute(kc_sub, "tenant_id", ctx.tenant_id, realm=realm)

    user = create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=body.username,
        full_name=body.full_name or None,
        email=body.email,
        role=body.role,
        hospital_id=ctx.tenant_id,
        force_password_change=False,
    )

    return HospitalUserOut(
        keycloak_sub=user.keycloak_sub,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        hospital_id=user.hospital_id,
        is_active=user.is_active,
        force_password_change=user.force_password_change,
    )


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
    user = db.query(User).filter(User.keycloak_sub == sub).first()
    if not user or user.hospital_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this hospital")

    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    if body.username is not None or body.email is not None or (body.full_name is not None and body.full_name.strip()) or body.is_active is not None:
        await update_keycloak_user(
            user.keycloak_sub,
            username=body.username,
            email=body.email,
            full_name=body.full_name,
            enabled=body.is_active,
            realm=realm,
        )
    if body.password is not None:
        await set_user_password(user.keycloak_sub, body.password, realm=realm)
    if body.role is not None:
        predefined = {"hospital_admin", "hospital_user", "nurse", "clinician", "doctor", "patient"}
        if body.role not in predefined:
            await ensure_roles([body.role], realm=realm)
        role_map = {
            "hospital_admin": ["hospital_admin", "hospital_user"],
            "hospital_user": ["hospital_user"],
            "nurse": ["nurse", "hospital_user"],
            "clinician": ["clinician", "hospital_user"],
            "doctor": ["doctor", "hospital_user"],
            "patient": ["patient", "hospital_user"],
        }
        target_roles = role_map.get(body.role, [body.role, "hospital_user"])
        await assign_user_roles(user.keycloak_sub, target_roles, realm=realm)

    updated = update_local_user(
        db=db,
        keycloak_sub=sub,
        username=body.username,
        full_name=body.full_name,
        email=body.email,
        role=body.role,
        is_active=body.is_active,
        force_password_change=body.force_password_change,
    )
    return HospitalUserOut(
        keycloak_sub=updated.keycloak_sub,
        username=updated.username,
        full_name=updated.full_name,
        email=updated.email,
        role=updated.role,
        hospital_id=updated.hospital_id,
        is_active=updated.is_active,
        force_password_change=updated.force_password_change,
    )


@router.delete("/users/{sub}", status_code=204, tags=["Users"])
@limiter.limit("30/minute")
async def delete_hospital_user(
    request: Request,
    sub: str,
    db: Session = Depends(get_tenant_db_for_request),
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    user = db.query(User).filter(User.keycloak_sub == sub).first()
    if not user or user.hospital_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this hospital")

    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    kc_deleted = await delete_keycloak_user(user.username or user.email or "", realm=realm)
    if not kc_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in Keycloak")
    delete_local_user(db, sub)


# ---------------------------------------------------------------------------
# Role management (within the tenant's Keycloak realm)
# ---------------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleOut], tags=["Roles"])
@limiter.limit("60/minute")
async def list_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[RoleOut]:
    """List all realm-level roles in the tenant's Keycloak realm."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        roles = await get_realm_roles(realm)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list roles: {e}",
        )
    return [RoleOut.model_validate(r) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=201, tags=["Roles"])
@limiter.limit("30/minute")
async def create_role(
    request: Request,
    body: RoleCreate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> RoleOut:
    """Create a new realm-level role in the tenant's Keycloak realm."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        role = await create_realm_role(realm, body.name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create role: {e}",
        )
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
    """Update a realm-level role name in the tenant's Keycloak realm."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        await update_realm_role(realm, role_name, body.name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update role: {e}",
        )
    return {"detail": f"Role '{role_name}' renamed to '{body.name}'"}


@router.delete("/roles/{role_name}", status_code=204, tags=["Roles"])
@limiter.limit("30/minute")
async def delete_role(
    request: Request,
    role_name: str,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    """Delete a realm-level role from the tenant's Keycloak realm."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    realm = _get_tenant_realm(master_db, ctx.tenant_id)
    try:
        await delete_realm_role(realm, role_name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete role: {e}",
        )


# ---------------------------------------------------------------------------
# Global roles listing (hospital admin read-only)
# ---------------------------------------------------------------------------


@router.get("/global-roles", response_model=list[GlobalRoleOut], tags=["Global Roles"])
@limiter.limit("60/minute")
async def list_global_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[GlobalRoleOut]:
    """List global roles available to all tenants (created by superadmin)."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    result = master_db.execute(
        text("SELECT global_role_id, name, description, scope, created_at, updated_at FROM global_roles ORDER BY created_at DESC")
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


# ---------------------------------------------------------------------------
# Tenant role management (stored in Master DB, scoped to tenant)
# ---------------------------------------------------------------------------


@router.post("/tenant-roles", response_model=TenantRoleOut, status_code=201, tags=["Tenant Roles"])
@limiter.limit("30/minute")
async def create_tenant_role(
    request: Request,
    body: TenantRoleCreate,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> TenantRoleOut:
    """Create a new role within the tenant. Checks if role already exists in global roles first."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")

    # Check if role already exists in global roles (superadmin-created)
    existing_global = master_db.execute(
        text("SELECT 1 FROM global_roles WHERE name = :name"),
        {"name": body.name},
    ).scalar()
    if existing_global:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Role '{body.name}' already exists as a global role. "
                   f"Use it directly instead of creating a duplicate.",
        )

    # Check tenant-specific role doesn't already exist
    existing = master_db.query(TenantRoleModel).filter(
        TenantRoleModel.tenant_id == ctx.tenant_id,
        TenantRoleModel.name == body.name,
    ).first()
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
    return TenantRoleOut.model_validate(role)


@router.get("/tenant-roles", response_model=list[TenantRoleOut], tags=["Tenant Roles"])
@limiter.limit("60/minute")
async def list_tenant_roles(
    request: Request,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[TenantRoleOut]:
    """List all roles created within this tenant."""
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
    """Update a tenant-specific role."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    role = master_db.query(TenantRoleModel).filter(
        TenantRoleModel.tenant_role_id == role_id,
        TenantRoleModel.tenant_id == ctx.tenant_id,
    ).first()
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant role not found")

    if body.name is not None:
        existing = master_db.query(TenantRoleModel).filter(
            TenantRoleModel.tenant_id == ctx.tenant_id,
            TenantRoleModel.name == body.name,
            TenantRoleModel.tenant_role_id != role_id,
        ).first()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Role '{body.name}' already exists in this tenant")
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.scope is not None:
        role.scope = body.scope

    master_db.commit()
    master_db.refresh(role)
    return TenantRoleOut.model_validate(role)


@router.delete("/tenant-roles/{role_id}", status_code=204, tags=["Tenant Roles"])
@limiter.limit("30/minute")
async def delete_tenant_role(
    request: Request,
    role_id: UUID,
    master_db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    """Delete a tenant-specific role."""
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    role = master_db.query(TenantRoleModel).filter(
        TenantRoleModel.tenant_role_id == role_id,
        TenantRoleModel.tenant_id == ctx.tenant_id,
    ).first()
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant role not found")
    master_db.delete(role)
    master_db.commit()
