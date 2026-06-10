from __future__ import annotations

from typing import Any

from app.messaging.publisher import publish_event

async def publish_tenant_created(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.created", {"tenant_id": tenant_id, **payload})

async def publish_tenant_suspended(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.suspended", {"tenant_id": tenant_id, **payload})
