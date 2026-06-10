"""
Event Subscriber for Consultation Service.

Consumes:
- triage.completed: Updates consultation with triage data.
- lab.result_ready: Updates consultation with lab results.
- radiology.report_ready: Updates consultation with radiology report.
"""

from app.messaging.subscriber import start_consumer

async def handle_triage_completed(triage_id: str, tenant_id: str) -> None:
    """Placeholder: Handle triage.completed event."""
    pass

async def handle_lab_result_ready(result_id: str, tenant_id: str) -> None:
    """Placeholder: Handle lab.result_ready event."""
    pass

async def handle_radiology_report_ready(report_id: str, tenant_id: str) -> None:
    """Placeholder: Handle radiology.report_ready event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "triage.completed":
        await handle_triage_completed(payload["triage_id"], payload["tenant_id"])
    elif routing_key == "lab.result_ready":
        await handle_lab_result_ready(payload["result_id"], payload["tenant_id"])
    elif routing_key == "radiology.report_ready":
        await handle_radiology_report_ready(payload["report_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="consultation-service",
        routing_keys=["triage.completed", "lab.result_ready", "radiology.report_ready"],
        handler=_dispatch,
    )
