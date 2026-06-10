"""
Event Publisher for Reception Service.

Publishes:
- patient.registered: When a new patient is registered.
- visit.created: When a new visit is created for a patient.
"""

from app.messaging.publisher import publish_event

async def publish_patient_registered(patient_id: str, tenant_id: str) -> None:
    """Placeholder: Publish patient.registered event."""
    await publish_event("patient.registered", {"patient_id": patient_id, "tenant_id": tenant_id})

async def publish_visit_created(visit_id: str, patient_id: str, tenant_id: str) -> None:
    """Placeholder: Publish visit.created event."""
    await publish_event("visit.created", {"visit_id": visit_id, "patient_id": patient_id, "tenant_id": tenant_id})
