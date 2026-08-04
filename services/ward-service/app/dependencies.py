"""FastAPI dependencies for ward-service."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.tenant import get_tenant_session


async def get_tenant_db_for_request(
    ctx: TenantContext = Depends(get_current_tenant),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session for the tenant resolved from the JWT."""
    if not ctx.tenant_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="No tenant association found in token",
        )
    async for session in get_tenant_session(ctx.tenant_id):
        yield session
