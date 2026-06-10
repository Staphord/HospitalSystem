"""
Event Publisher for Radiology Service.

Publishes:
- radiology.report_ready: When a radiology report is ready.
"""

from app.messaging.publisher import publish_event

async def publish_radiology_report_ready(report_id: str, tenant_id: str) -> None:
    """Placeholder: Publish radiology.report_ready event."""
    await publish_event("radiology.report_ready", {"report_id": report_id, "tenant_id": tenant_id})
