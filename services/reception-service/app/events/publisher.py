"""
Event Publisher for Reception Service.

Publishes:
- patient.registered: When a new patient is registered.

visit.created is published by visit-service itself, not here — reception
only orchestrates the call.
"""

from app.messaging.publisher import publish_event

async def publish_patient_registered(patient_id: str, tenant_id: str) -> None:
    """Placeholder: Publish patient.registered event."""
    await publish_event("patient.registered", {"patient_id": patient_id, "tenant_id": tenant_id})
