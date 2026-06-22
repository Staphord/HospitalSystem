from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.services import auth as auth_service
from app.services.keycloak_admin import update_keycloak_user, set_user_password, update_local_user
from app.api.v1.users.schemas import UserUpdate, PasswordChange

router = APIRouter()


@router.get("/me")
@limiter.limit("30/minute")
async def me(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    user = getattr(request.state, "user", None)
    is_super = user and user.get("super_admin_id")
    if is_super:
        from app.models.admin import SuperAdmin
        db_admin = db.query(SuperAdmin).filter(SuperAdmin.super_admin_id == user["super_admin_id"]).first()
        db_full_name = db_admin.full_name if db_admin else "Super Admin"
        db_email = db_admin.email if db_admin else "admin@hospital.com"
        db_mfa = db_admin.mfa_enabled if db_admin else False
        return {
            "sub": str(user["super_admin_id"]),
            "username": user["username"],
            "preferred_username": user["username"],
            "email": db_email,
            "full_name": db_full_name,
            "roles": [user["role"]],
            "role": user["role"],
            "tenant_id": None,
            "is_super_admin": True,
            "scope": ctx.scope,
            "mfa_enabled": db_mfa,
        }

    # Handle impersonation tokens: return a synthetic profile for the session
    is_impersonating = ctx.raw_token.get("impersonator") or ctx.raw_token.get("impersonation")
    if is_impersonating:
        hospital_name = None
        if ctx.tenant_id:
            from app.models.master import Tenant as TenantModel
            tenant_record = db.query(TenantModel).filter(TenantModel.tenant_id == ctx.tenant_id).first()
            if tenant_record:
                hospital_name = tenant_record.name
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

    # Pick the most specific role as the primary one
    role_priority = ["super_admin", "hospital_admin", "nurse", "clinician", "doctor", "patient", "hospital_user"]
    primary_role = None
    if ctx.is_super_admin:
        primary_role = "super_admin"
    else:
        for pr in role_priority:
            if pr in user_roles:
                primary_role = pr
                break
        if not primary_role and user_roles:
            primary_role = user_roles[0]
        if not primary_role:
            primary_role = "hospital_user"

    # Fetch user details from DB to get the actual full_name and username
    db_username = ctx.preferred_username
    db_full_name = ctx.preferred_username.capitalize() if ctx.preferred_username else "User"
    db_mfa = False

    if ctx.is_super_admin:
        from app.models.admin import SuperAdmin
        db_admin = db.query(SuperAdmin).filter(
            (SuperAdmin.email == ctx.email) | (SuperAdmin.username == ctx.preferred_username)
        ).first()
        if db_admin:
            db_username = db_admin.username or db_username
            db_full_name = db_admin.full_name or db_full_name
            db_mfa = db_admin.mfa_enabled
    elif ctx.tenant_id:
        from app.services.provision import get_tenant_db_session
        from app.models.user import User
        tenant_db = get_tenant_db_session(ctx.tenant_id)
        try:
            db_user = tenant_db.query(User).filter(User.keycloak_sub == ctx.user_sub).first()
            if db_user:
                db_username = db_user.username or db_username
                db_full_name = db_user.full_name or db_full_name
                db_mfa = db_user.mfa_enabled
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
        "is_super_admin": ctx.is_super_admin,
        "scope": ctx.scope,
        "mfa_enabled": db_mfa,
    }


@router.put("/me")
@limiter.limit("10/minute")
async def update_me(
    request: Request,
    body: UserUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    # Check if user is a regular tenant user or super admin
    user = getattr(request.state, "user", None)
    is_super = user and user.get("super_admin_id")

    # Update in Keycloak
    try:
        await update_keycloak_user(
            user_id=ctx.user_sub,
            username=body.username,
            email=body.email,
            full_name=body.full_name,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update user in identity provider: {str(e)}")

    # Update local DB if tenant user
    if not is_super and ctx.tenant_id:
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
    # Verify current password using login service
    try:
        await auth_service.login(
            username=ctx.preferred_username,
            password=body.current_password,
            db=db,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid current password")

    # Update to new password
    try:
        await set_user_password(user_id=ctx.user_sub, password=body.new_password)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to change password: {str(e)}")

    return {"detail": "Password changed successfully"}
