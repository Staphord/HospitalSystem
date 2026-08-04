"""
Event Publisher for Radiology Service.

Publishes:
- radiology.report_ready: When a radiology report is reported/verified (FR-32).
"""

from app.messaging.publisher import publish_event


async def publish_radiology_report_ready(
    report_id: str,
    request_id: str,
    tenant_id: str,
    status: str,
) -> None:
    await publish_event(
        "radiology.report_ready",
        {
            "report_id": report_id,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "status": status,
        },
    )
