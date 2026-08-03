"""
Event Subscriber for Radiology Service.

Consumes:
- investigation.requested: Logged when a radiology investigation is requested.
  Worklist is sourced from investigation_requests; no cross-service side effects.
"""

import logging

from app.messaging.subscriber import start_consumer

logger = logging.getLogger("service.radiology.events")


async def handle_investigation_requested(payload: dict) -> None:
    investigation_type = payload.get("investigation_type") or payload.get("request_type")
    if investigation_type not in ("radiology",):
        return
    request_id = payload.get("request_id") or payload.get("investigation_id")
    tenant_id = payload.get("tenant_id")
    logger.info(
        "Received investigation.requested for radiology request_id=%s tenant_id=%s",
        request_id,
        tenant_id,
    )


async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "investigation.requested":
        await handle_investigation_requested(payload)


async def start_subscriber() -> None:
    await start_consumer(
        service_name="radiology-service",
        routing_keys=["investigation.requested"],
        handler=_dispatch,
    )
