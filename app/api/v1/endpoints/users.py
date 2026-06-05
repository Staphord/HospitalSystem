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
    return {
        "sub": ctx.user_sub,
        "preferred_username": ctx.preferred_username,
        "email": ctx.email,
        "roles": ctx.roles,
        "tenant_id": ctx.tenant_id,
        "is_super_admin": ctx.is_super_admin,
        "scope": ctx.scope,
    }
