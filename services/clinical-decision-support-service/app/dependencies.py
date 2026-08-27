from fastapi import Depends

from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.tenant import get_tenant_session


async def get_tenant_db(ctx: TenantContext = Depends(get_current_tenant)):
    """Yield an async session for the tenant resolved from the verified token.

    The tenant is never taken from a path, a query string, a body field, or a
    header supplied by the browser. It comes from the token claim that
    get_current_tenant verified, so a caller cannot point this service at
    another hospital's database.
    """
    async for session in get_tenant_session(ctx.tenant_id):
        yield session
