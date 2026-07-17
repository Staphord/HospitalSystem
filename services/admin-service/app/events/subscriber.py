"""No-op / future event consumer for admin-service."""

from __future__ import annotations

import logging

from app.messaging.subscriber import start_consumer

logger = logging.getLogger("admin_service.events.subscriber")


async def _dispatch(routing_key: str, payload: dict) -> None:
    logger.debug("admin-service received event %s (ignored)", routing_key)


async def start_subscriber() -> None:
    await start_consumer(
        service_name="admin-service",
        routing_keys=["user.created", "user.deactivated"],
        handler=_dispatch,
    )
