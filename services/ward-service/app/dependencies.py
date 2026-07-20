"""FastAPI dependencies for ward-service."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.db.tenant import get_tenant_session


def resolve_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> str:
    """Resolve tenant without JWT (temporary open ward APIs)."""
    tid = (
        getattr(request.state, "tenant_id", None)
        or x_tenant_id
        or getattr(settings, "dev_tenant_id", None)
        or settings.default_hospital_id
    )
    if not tid or tid == "default-hospital":
        tid = getattr(settings, "dev_tenant_id", None) or "hosp-ac224699"
    request.state.tenant_id = tid
    return tid


async def get_tenant_db(
    request: Request,
    tenant_id: str = Depends(resolve_tenant_id),
):
    """Yield an async SQLAlchemy session for the resolved tenant database."""
    if not tenant_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Missing tenant id (set X-Tenant-ID header or DEV_TENANT_ID)",
        )
    async for session in get_tenant_session(tenant_id):
        yield session
