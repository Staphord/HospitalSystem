from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant

router = APIRouter()


@router.get("/me")
@limiter.limit("30/minute")
async def me(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict:
    user = getattr(request.state, "user", None)
    if user and user.get("super_admin_id"):
        return {
            "super_admin_id": user["super_admin_id"],
            "username": user["username"],
            "role": user["role"],
            "is_super_admin": True,
            "scope": ctx.scope,
        }
    # Derive primary role from Keycloak realm roles
    raw_roles = ctx.roles or []
    # Filter out system roles
    system_roles = {"default-roles-hosp-", "offline_access", "uma_authorization"}
    user_roles = [r for r in raw_roles if not any(r.startswith(s) for s in system_roles) and r not in system_roles]

    # Pick the most specific role as the primary one
    role_priority = ["hospital_admin", "nurse", "clinician", "doctor", "patient", "hospital_user"]
    primary_role = None
    for pr in role_priority:
        if pr in user_roles:
            primary_role = pr
            break
    if not primary_role and user_roles:
        primary_role = user_roles[0]
    if not primary_role:
        primary_role = "hospital_user"

    return {
        "sub": ctx.user_sub,
        "preferred_username": ctx.preferred_username,
        "email": ctx.email,
        "roles": ctx.roles,
        "role": primary_role,
        "tenant_id": ctx.tenant_id,
        "is_super_admin": ctx.is_super_admin,
        "scope": ctx.scope,
    }
