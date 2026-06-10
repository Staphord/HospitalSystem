"""
Event Publisher for Triage Service.

Publishes:
- triage.completed: When a triage assessment is completed.
"""

from app.messaging.publisher import publish_event

async def publish_triage_completed(triage_id: str, visit_id: str, tenant_id: str) -> None:
    """Placeholder: Publish triage.completed event."""
    await publish_event("triage.completed", {"triage_id": triage_id, "visit_id": visit_id, "tenant_id": tenant_id})
