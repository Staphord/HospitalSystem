# Events consumed by notification-service

from app.messaging.subscriber import start_consumer

async def handle_lab_critical_value(lab_result_id: str, tenant_id: str) -> None:
    """Stub: process lab.critical_value event."""
    pass

async def handle_radiology_report_ready(report_id: str, tenant_id: str) -> None:
    """Stub: process radiology.report_ready event."""
    pass

async def handle_stock_low(item_id: str, tenant_id: str) -> None:
    """Stub: process stock.low event."""
    pass

async def handle_patient_admitted(admission_id: str, tenant_id: str) -> None:
    """Stub: process patient.admitted event."""
    pass

async def handle_prescription_issued(prescription_id: str, tenant_id: str) -> None:
    """Stub: process prescription.issued event."""
    pass

async def handle_tenant_created(tenant_id: str) -> None:
    """Stub: process tenant.created event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "lab.critical_value":
        await handle_lab_critical_value(payload["lab_result_id"], payload["tenant_id"])
    elif routing_key == "radiology.report_ready":
        await handle_radiology_report_ready(payload["report_id"], payload["tenant_id"])
    elif routing_key == "stock.low":
        await handle_stock_low(payload["item_id"], payload["tenant_id"])
    elif routing_key == "patient.admitted":
        await handle_patient_admitted(payload["admission_id"], payload["tenant_id"])
    elif routing_key == "prescription.issued":
        await handle_prescription_issued(payload["prescription_id"], payload["tenant_id"])
    elif routing_key == "tenant.created":
        await handle_tenant_created(payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="notification-service",
        routing_keys=["lab.critical_value", "radiology.report_ready", "stock.low", "patient.admitted", "prescription.issued", "tenant.created"],
        handler=_dispatch,
    )
