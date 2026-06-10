"""
Event Subscriber for Laboratory Service.

Consumes:
- investigation.requested: Triggers lab processing when an investigation is requested.
"""

from app.messaging.subscriber import start_consumer

async def handle_investigation_requested(investigation_id: str, tenant_id: str) -> None:
    """Placeholder: Handle investigation.requested event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "investigation.requested":
        await handle_investigation_requested(payload["investigation_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="laboratory-service",
        routing_keys=["investigation.requested"],
        handler=_dispatch,
    )
