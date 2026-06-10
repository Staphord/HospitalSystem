from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db
from app.core.security import (
    TokenPayload,
    get_current_active_user,
    require_role as _require_role,
)
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.tenant_sync import get_tenant_db_sync


def get_tenant_db():
    yield from _get_db()


def get_tenant_db_for_request(
    ctx: TenantContext = Depends(get_current_tenant),
):
    """Get a database session for the tenant from the JWT context.
    
    This routes to the tenant-specific database instead of the master database.
    """
    if not ctx.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenant association found in token",
        )
    yield from get_tenant_db_sync(ctx.tenant_id)


async def get_current_user(
    user: TokenPayload = Depends(get_current_active_user),
) -> TokenPayload:
    return user


def require_role(role: str):
    return _require_role(role)
