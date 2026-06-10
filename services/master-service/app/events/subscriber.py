"""Event Subscriber for Master Service.

Consumes:
- tenant.created: Provisions a new isolated database for the tenant.
- tenant.suspended: Logs suspension (placeholder).
"""

from __future__ import annotations

import logging

from app.messaging.subscriber import start_consumer
from app.services.provision import provision_tenant_database

logger = logging.getLogger(__name__)


async def handle_tenant_created(payload: dict) -> None:
    """Handle tenant.created — provision a new PostgreSQL database."""
    tenant_id = payload.get("tenant_id", "")
    name = payload.get("name", "")
    logger.info("Received tenant.created event for tenant_id=%s", tenant_id)
    try:
        await provision_tenant_database(tenant_id=tenant_id, name=name)
        logger.info("[OK] Provisioning complete for tenant_id=%s", tenant_id)
    except Exception as exc:
        logger.error("[FAIL] Provisioning failed for tenant_id=%s: %s", tenant_id, exc)


async def handle_tenant_suspended(payload: dict) -> None:
    """Placeholder: Handle tenant.suspended."""
    tenant_id = payload.get("tenant_id", "")
    reason = payload.get("reason", "")
    logger.info("Received tenant.suspended event for tenant_id=%s reason=%s", tenant_id, reason)


async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "tenant.created":
        await handle_tenant_created(payload)
    elif routing_key == "tenant.suspended":
        await handle_tenant_suspended(payload)


async def start_subscriber() -> None:
    await start_consumer(
        service_name="master-service",
        routing_keys=["tenant.created", "tenant.suspended"],
        handler=_dispatch,
    )
