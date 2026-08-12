# Events consumed by billing-service

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from sqlalchemy import select, text

from app.db.tenant import get_tenant_session
from app.messaging.subscriber import start_consumer
from app.models.billing import Bill
from app.services.billing import apply_ward_charge_on_discharge, apply_drug_charge

logger = logging.getLogger("billing_service.events")


async def handle_visit_created(visit_id: str, tenant_id: str, patient_id: str | None = None) -> None:
    """Initialize a new Bill for the visit."""
    if not patient_id:
        logger.error("visit.created missing patient_id for visit=%s", visit_id)
        return
    visit_uuid = uuid.UUID(visit_id)
    patient_uuid = uuid.UUID(patient_id)
    
    async for session in get_tenant_session(tenant_id):
        # Check if bill already exists for this visit
        bill_result = await session.execute(
            select(Bill).where(Bill.visit_id == visit_uuid)
        )
        bill = bill_result.scalars().first()
        if not bill:
            bill = Bill(
                bill_id=uuid.uuid4(),
                visit_id=visit_uuid,
                patient_id=patient_uuid,
                status="open",
                total_amount=Decimal("0.00"),
            )
            session.add(bill)
            await session.commit()
            logger.info("Initialized open bill for visit=%s tenant=%s", visit_id, tenant_id)
        break


async def handle_drug_dispensed(dispense_id: str, tenant_id: str) -> None:
    """Apply drug dispensed charges to patient visit bill."""
    async for session in get_tenant_session(tenant_id):
        try:
            await apply_drug_charge(
                session,
                dispensing_id=dispense_id,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.exception(
                "Failed drug charge for dispensing=%s tenant=%s", dispense_id, tenant_id
            )
        break


async def handle_patient_admitted(admission_id: str, tenant_id: str) -> None:
    """Stub: process patient.admitted event (charges applied on discharge)."""
    pass


async def handle_patient_discharged(payload: dict) -> None:
    admission_id = payload["admission_id"]
    tenant_id = payload["tenant_id"]
    patient_id = payload.get("patient_id")
    if not patient_id:
        logger.error("patient.discharged missing patient_id admission=%s", admission_id)
        return
    los = float(payload.get("length_of_stay_days") or 0)
    visit_id = payload.get("visit_id")
    async for session in get_tenant_session(tenant_id):
        try:
            await apply_ward_charge_on_discharge(
                session,
                admission_id=admission_id,
                patient_id=patient_id,
                tenant_id=tenant_id,
                length_of_stay_days=los,
                visit_id=visit_id,
            )
        except Exception:
            logger.exception(
                "Failed ward charge for admission=%s tenant=%s", admission_id, tenant_id
            )
        break


async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "visit.created":
        await handle_visit_created(payload["visit_id"], payload["tenant_id"], payload.get("patient_id"))
    elif routing_key == "drug.dispensed":
        await handle_drug_dispensed(payload.get("dispensing_id") or payload.get("dispense_id"), payload["tenant_id"])
    elif routing_key == "patient.admitted":
        await handle_patient_admitted(payload["admission_id"], payload["tenant_id"])
    elif routing_key == "patient.discharged":
        await handle_patient_discharged(payload)


async def start_subscriber() -> None:
    await start_consumer(
        service_name="billing-service",
        routing_keys=["visit.created", "drug.dispensed", "patient.admitted", "patient.discharged"],
        handler=_dispatch,
    )
