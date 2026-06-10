# Events consumed by billing-service

from app.messaging.subscriber import start_consumer

async def handle_visit_created(visit_id: str, tenant_id: str) -> None:
    """Stub: process visit.created event."""
    pass

async def handle_drug_dispensed(dispense_id: str, tenant_id: str) -> None:
    """Stub: process drug.dispensed event."""
    pass

async def handle_patient_admitted(admission_id: str, tenant_id: str) -> None:
    """Stub: process patient.admitted event."""
    pass

async def handle_patient_discharged(admission_id: str, tenant_id: str) -> None:
    """Stub: process patient.discharged event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "visit.created":
        await handle_visit_created(payload["visit_id"], payload["tenant_id"])
    elif routing_key == "drug.dispensed":
        await handle_drug_dispensed(payload["dispense_id"], payload["tenant_id"])
    elif routing_key == "patient.admitted":
        await handle_patient_admitted(payload["admission_id"], payload["tenant_id"])
    elif routing_key == "patient.discharged":
        await handle_patient_discharged(payload["admission_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="billing-service",
        routing_keys=["visit.created", "drug.dispensed", "patient.admitted", "patient.discharged"],
        handler=_dispatch,
    )
