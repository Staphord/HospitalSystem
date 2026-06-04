from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db
from app.core.security import (
    TokenPayload,
    get_current_active_user,
    require_role as _require_role,
)
from app.db.tenant import get_tenant_db


def get_master_db() -> Session:
    yield from _get_db()


def get_tenant_session(
    user: TokenPayload = Depends(get_current_active_user),
) -> Session:
    yield from get_tenant_db(user.sub)


def require_role(role: str):
    return _require_role(role)
