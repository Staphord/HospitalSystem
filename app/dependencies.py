from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.security import (
    TokenPayload,
    get_current_active_user,
    require_role as _require_role,
)
from app.db.tenant import get_tenant_session


def get_master_db() -> Session:
    yield from _get_db()


async def get_tenant_session_dep(
    ctx: TenantContext = Depends(get_current_tenant),
):
    async for session in get_tenant_session(ctx.tenant_id):
        yield session


def require_role(role: str):
    return _require_role(role)
