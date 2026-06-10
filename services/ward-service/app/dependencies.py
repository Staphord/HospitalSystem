from fastapi import Depends
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.security import TokenPayload, get_current_active_user
from app.db.tenant import get_tenant_session

async def get_tenant_db(ctx: TenantContext = Depends(get_current_tenant)):
    """Yield an async SQLAlchemy session for the resolved tenant database."""
    async for session in get_tenant_session(ctx.tenant_id):
        yield session

async def get_current_user(user: TokenPayload = Depends(get_current_active_user)) -> TokenPayload:
    """Return the currently authenticated user from the JWT token."""
    return user
