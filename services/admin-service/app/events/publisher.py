from __future__ import annotations

from typing import Any

from app.messaging.publisher import publish_event

async def publish_user_created(tenant_id: str, user_sub: str, payload: dict[str, Any]) -> None:
    await publish_event("user.created", {"tenant_id": tenant_id, "user_sub": user_sub, **payload})

async def publish_user_deactivated(tenant_id: str, user_sub: str, payload: dict[str, Any]) -> None:
    await publish_event("user.deactivated", {"tenant_id": tenant_id, "user_sub": user_sub, **payload})
