from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.services import auth as auth_service
from app.services.keycloak_admin import update_keycloak_user, set_user_password, update_local_user
from app.api.v1.users.schemas import UserUpdate, PasswordChange

router = APIRouter()


def _derive_primary_role(ctx: TenantContext) -> str:
    """Derive the highest-priority role from the tenant context."""
    if ctx.is_super_admin:
        return "super_admin"
    raw_roles = ctx.roles or []
    system_roles = {"default-roles-hosp-", "offline_access", "uma_authorization"}
    user_roles = [
        r for r in raw_roles
        if not any(r.startswith(s) for s in system_roles) and r not in system_roles
    ]
    role_priority = ["super_admin", "hospital_admin", "nurse", "clinician", "doctor", "patient", "hospital_user"]
    for pr in role_priority:
        if pr in user_roles:
            return pr
    return user_roles[0] if user_roles else "hospital_user"


@router.get("/me")
@limiter.limit("30/minute")
async def me(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    primary_role = _derive_primary_role(ctx)
    is_super = ctx.is_super_admin
    mfa_enabled = False

    if is_super:
        from app.models.admin import SuperAdmin
        db_admin = db.query(SuperAdmin).filter(
            (SuperAdmin.email == ctx.email) | (SuperAdmin.username == ctx.preferred_username)
        ).first()
        db_full_name = db_admin.full_name if db_admin else ctx.preferred_username or "Super Admin"
        db_email = db_admin.email if db_admin else ctx.email or "admin@hospital.com"
        mfa_enabled = db_admin.mfa_enabled if db_admin else False
        return {
            "sub": ctx.user_sub,
            "username": db_admin.username if db_admin else ctx.preferred_username,
            "preferred_username": ctx.preferred_username,
            "email": db_email,
            "full_name": db_full_name,
            "roles": ctx.roles,
            "role": primary_role,
            "tenant_id": None,
            "is_super_admin": True,
            "scope": ctx.scope,
            "mfa_enabled": mfa_enabled,
        }


    # Handle impersonation tokens: return a synthetic profile for the session
    is_impersonating = ctx.raw_token.get("impersonator") or ctx.raw_token.get("impersonation")
    if is_impersonating:
        hospital_name = None
        if ctx.tenant_id:
            from app.models.master import Tenant as TenantModel
            tenant_record = db.query(TenantModel).filter(TenantModel.tenant_id == ctx.tenant_id).first()
            if tenant_record:
                hospital_name = tenant_record.hospital_name
        return {
            "sub": ctx.user_sub,
            "username": ctx.preferred_username or "superadmin",
            "preferred_username": ctx.preferred_username or "superadmin",
            "email": ctx.email or "support@hospitalflow.com",
            "full_name": (ctx.preferred_username or "Super Admin").replace("_", " ").title(),
            "roles": ["hospital_admin"],
            "role": "hospital_admin",
            "tenant_id": ctx.tenant_id,
            "hospital_name": hospital_name,
            "is_super_admin": False,
            "scope": ctx.scope,
        }

    # Derive primary role from Keycloak realm roles
    raw_roles = ctx.roles or []
    # Filter out system roles
    system_roles = {"default-roles-hosp-", "offline_access", "uma_authorization"}
    user_roles = [r for r in raw_roles if not any(r.startswith(s) for s in system_roles) and r not in system_roles]


    # Tenant user path
    db_username = ctx.preferred_username
    db_full_name = ctx.preferred_username.capitalize() if ctx.preferred_username else "User"

    if ctx.tenant_id:
        from app.services.provision import get_tenant_db_session
        from app.models.user import User
        tenant_db = get_tenant_db_session(ctx.tenant_id)
        try:
            db_user = tenant_db.query(User).filter(User.keycloak_sub == ctx.user_sub).first()
            if db_user:
                db_username = db_user.username or db_username
                db_full_name = db_user.full_name or db_full_name
                mfa_enabled = db_user.mfa_enabled
        finally:
            tenant_db.close()

    return {
        "sub": ctx.user_sub,
        "username": db_username,
        "preferred_username": ctx.preferred_username,
        "email": ctx.email,
        "full_name": db_full_name,
        "roles": ctx.roles,
        "role": primary_role,
        "tenant_id": ctx.tenant_id,
        "is_super_admin": False,
        "scope": ctx.scope,
        "mfa_enabled": mfa_enabled,
    }


@router.put("/me")
@limiter.limit("10/minute")
async def update_me(
    request: Request,
    body: UserUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    is_super = ctx.is_super_admin
    realm = "master" if is_super else (ctx.tenant_id or settings.keycloak_realm)

    # Update in Keycloak
    try:
        await update_keycloak_user(
            user_id=ctx.user_sub,
            username=body.username,
            email=body.email,
            full_name=body.full_name,
            realm=realm,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update user in identity provider: {str(e)}")

    # Update local DB
    if is_super:
        from app.models.admin import SuperAdmin
        admin = db.query(SuperAdmin).filter(SuperAdmin.super_admin_id == ctx.user_sub).first()
        if admin:
            if body.username:
                admin.username = body.username
            if body.email:
                admin.email = body.email
            if body.full_name:
                admin.full_name = body.full_name
            db.commit()
    elif ctx.tenant_id:
        from app.services.provision import get_tenant_db_session
        tenant_db = get_tenant_db_session(ctx.tenant_id)
        try:
            update_local_user(
                db=tenant_db,
                keycloak_sub=ctx.user_sub,
                username=body.username,
                email=body.email,
                full_name=body.full_name,
            )
        finally:
            tenant_db.close()

    return {"detail": "Profile updated successfully"}


@router.post("/me/password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: PasswordChange,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    is_super = ctx.is_super_admin
    realm = "master" if is_super else (ctx.tenant_id or settings.keycloak_realm)

    # Verify current password using login service
    try:
        await auth_service.login(
            username=ctx.preferred_username,
            password=body.current_password,
            db=db,
            realm=realm,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid current password")

    # Update to new password
    try:
        await set_user_password(user_id=ctx.user_sub, password=body.new_password, realm=realm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to change password: {str(e)}")

    return {"detail": "Password changed successfully"}
