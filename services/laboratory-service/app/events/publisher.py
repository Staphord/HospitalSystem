"""
Event Publisher for Laboratory Service.

Publishes:
- lab.result_ready: When a lab result is ready.
- lab.critical_value: When a critical value is detected.
"""

from app.messaging.publisher import publish_event

async def publish_lab_result_ready(
    result_id: str,
    tenant_id: str,
    request_id: str = "",
    test_name: str = "",
    requested_by: str = "",
    is_critical: bool = False,
    patient_id: str = "",
) -> None:
    """Publish lab.result_ready event with order details."""
    payload = {
        "result_id": result_id,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "test_name": test_name,
        "requested_by": requested_by,
        "is_critical": is_critical,
        "patient_id": patient_id,
    }
    await publish_event("lab.result_ready", payload)

async def publish_lab_critical_value(result_id: str, tenant_id: str) -> None:
    """Placeholder: Publish lab.critical_value event."""
    await publish_event("lab.critical_value", {"result_id": result_id, "tenant_id": tenant_id})
