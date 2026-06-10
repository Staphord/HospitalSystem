"""Event Publisher for Auth Service.

Publishes tenant-related events.
"""

from __future__ import annotations

from typing import Any

from app.messaging.publisher import publish_event


async def publish_tenant_created(
    tenant_id: str,
    name: str,
    admin_email: str,
    admin_username: str,
    hospital_id: str | None = None,
) -> None:
    """Publish tenant.created event when a new hospital tenant signs up."""
    await publish_event(
        "tenant.created",
        {
            "tenant_id": tenant_id,
            "name": name,
            "admin_email": admin_email,
            "admin_username": admin_username,
            "hospital_id": hospital_id,
        },
    )
