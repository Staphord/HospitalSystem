"""
Event Subscriber for Triage Service.

Consumes:
- visit.created: No action taken. Triage assessment creation is driven
  entirely by the triage nurse through the UI (see
  app/services/triage.py::record_triage_assessment), not by this event.
  Kept subscribed so the queue exists if a future workflow needs it.
"""

from app.messaging.subscriber import start_consumer

async def handle_visit_created(visit_id: str, tenant_id: str) -> None:
    """No-op: triage workflow is driven by direct API calls, not this event."""
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
