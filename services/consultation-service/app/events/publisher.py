"""
Event Publisher for Consultation Service.

Publishes:
- investigation.requested: When a doctor requests lab or radiology investigations.
- prescription.issued: When a prescription is issued.
"""

from app.messaging.publisher import publish_event

async def publish_investigation_requested(
    consultation_id: str,
    tenant_id: str,
    test_name: str = "",
    urgency: str = "routine",
    request_type: str = "laboratory",
    requested_by: str = "",
) -> None:
    """Publish investigation.requested event with complete order details."""
    await publish_event(
        "investigation.requested",
        {
            "consultation_id": consultation_id,
            "tenant_id": tenant_id,
            "test_name": test_name,
            "urgency": urgency,
            "request_type": request_type,
            "requested_by": requested_by,
        },
    )

async def publish_prescription_issued(prescription_id: str, consultation_id: str, tenant_id: str) -> None:
    """Placeholder: Publish prescription.issued event."""
    await publish_event("prescription.issued", {"prescription_id": prescription_id, "consultation_id": consultation_id, "tenant_id": tenant_id})
