"""
Event Subscriber for Triage Service.

Consumes:
- visit.created: Triggers triage workflow when a visit is created.
"""

from app.messaging.subscriber import start_consumer

async def handle_visit_created(visit_id: str, tenant_id: str) -> None:
    """Placeholder: Handle visit.created event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "visit.created":
        await handle_visit_created(payload["visit_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="triage-service",
        routing_keys=["visit.created"],
        handler=_dispatch,
    )
