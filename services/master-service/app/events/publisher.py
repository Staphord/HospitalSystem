from __future__ import annotations

from typing import Any

from app.messaging.publisher import publish_event


async def publish_tenant_created(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.created", {"tenant_id": tenant_id, **payload})


async def publish_tenant_suspended(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.suspended", {"tenant_id": tenant_id, **payload})


async def publish_tenant_activated(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.activated", {"tenant_id": tenant_id, **payload})


async def publish_tenant_reactivated(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("tenant.reactivated", {"tenant_id": tenant_id, **payload})


async def publish_announcement_created(payload: dict[str, Any]) -> None:
    await publish_event("announcement.created", payload)


async def publish_subscription_invoice_generated(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("subscription.invoice_generated", {"tenant_id": tenant_id, **payload})


async def publish_subscription_request_created(payload: dict[str, Any]) -> None:
    await publish_event("subscription_request.created", payload)


async def publish_subscription_request_processed(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("subscription_request.processed", {"tenant_id": tenant_id, **payload})


async def publish_subscription_invoice_overdue(tenant_id: str, payload: dict[str, Any]) -> None:
    await publish_event("subscription.invoice_overdue", {"tenant_id": tenant_id, **payload})
