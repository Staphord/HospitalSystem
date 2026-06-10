from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db as _get_db
from app.core.security import (
    TokenPayload,
    get_current_active_user,
    require_role as _require_role,
)


def get_master_db():
    yield from _get_db()


async def get_current_super_admin(
    user: TokenPayload = Depends(get_current_active_user),
) -> TokenPayload:
    roles = user.realm_access.get("roles", []) if user.realm_access else []
    if user.raw.get("type") == "superadmin":
        roles = [user.raw.get("role", "super_admin")]
    if "super_admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin required",
        )
    return user
