# Events published by ward-service

from app.messaging.publisher import publish_event

async def publish_patient_admitted(admission_id: str, tenant_id: str) -> None:
    """Stub: emit patient.admitted event."""
    await publish_event("patient.admitted", {"admission_id": admission_id, "tenant_id": tenant_id})

async def publish_patient_discharged(admission_id: str, tenant_id: str) -> None:
    """Stub: emit patient.discharged event."""
    await publish_event("patient.discharged", {"admission_id": admission_id, "tenant_id": tenant_id})
