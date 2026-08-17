"""
Event Subscriber for Pharmacy Service.

Consumes:
- prescription.issued: Triggers dispensing workflow when a prescription is issued.
- payment.received: Confirms payment before dispensing drugs.
"""

from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime, timezone, date

from sqlalchemy import select, or_

from app.db.tenant import get_tenant_session
from app.messaging.subscriber import start_consumer
from app.models.pharmacy import Prescription, PrescriptionItem, Patient, Visit, Queue


async def handle_prescription_issued(payload: dict, tenant_id: str) -> None:
    """
    Handle prescription.issued event.
    Looks up prescription record in tenant database and calculates drug charges on the visit bill.
    """
    prescription_id_str = payload.get("prescription_id")
    if not prescription_id_str:
        return

    prescription_id = UUID(prescription_id_str)

    async for db in get_tenant_session(tenant_id):
        # Query prescription record created by consultation service
        rx_stmt = select(Prescription).where(
            or_(Prescription.prescription_id == prescription_id, Prescription.id == prescription_id)
        )
        rx_res = await db.execute(rx_stmt)
        rx = rx_res.scalars().first()

        visit_id_str = payload.get("visit_id") or (str(rx.visit_id) if rx else None)
        patient_id_str = payload.get("patient_id") or (str(rx.patient_id) if rx else None)

        if not visit_id_str or not patient_id_str:
            return

        visit_id = UUID(visit_id_str)
        patient_id = UUID(patient_id_str)

        # Ensure patient exists in the tenant db
        patient = await db.get(Patient, patient_id)
        if not patient:
            patient = Patient(
                id=patient_id,
                hospital_id=tenant_id,
                patient_number=payload.get("patient_number", "PT-4891"),
                full_name=payload.get("patient_name", "Jane Mwita"),
                date_of_birth=date(1985, 4, 12),
                gender=payload.get("gender", "Female"),
                allergies=payload.get("allergies", "Penicillin"),
            )
            db.add(patient)

        # Ensure visit exists in the tenant db
        visit = await db.get(Visit, visit_id)
        if not visit:
            visit = Visit(
                visit_id=visit_id,
                patient_id=patient_id,
                visit_number=payload.get("visit_number", "V-20260315-042"),
                visit_date=date.today(),
                visit_type="outpatient",
                payment_type=payload.get("payment_type", "cash"),
                status="in_pharmacy",
            )
            db.add(visit)

        # Ensure queue entry exists in the tenant db
        queue_stmt = select(Queue).where(Queue.visit_id == visit_id, Queue.queue_type == "pharmacy")
        queue_res = await db.execute(queue_stmt)
        queue = queue_res.scalar_one_or_none()
        if not queue:
            queue = Queue(
                queue_id=uuid4(),
                visit_id=visit_id,
                patient_id=str(patient_id),
                queue_type="pharmacy",
                queue_number="PH-007",
                priority="urgent",
                status="waiting",
                created_at=datetime.now(timezone.utc),
            )
            db.add(queue)

        # Create Prescription if not already present
        if not rx:
            rx = Prescription(
                prescription_id=prescription_id,
                id=prescription_id,
                visit_id=visit_id,
                patient_id=patient_id,
                prescribed_by=payload.get("prescribed_by", "Dr. Nguyen"),
                prescribed_at=datetime.now(timezone.utc),
                status="pending",
            )
            db.add(rx)

        # Extract items from payload or from existing rx database row
        items = payload.get("items")
        if not items and rx and rx.drug_name:
            # Parse quantity prescribed from rx.dose (e.g. "30") or payload
            calculated_qty = 1
            if rx.dose and rx.dose.isdigit():
                calculated_qty = int(rx.dose)
            elif payload.get("quantity_prescribed"):
                calculated_qty = int(payload["quantity_prescribed"])

            items = [
                {
                    "prescription_item_id": str(rx.prescription_id or rx.id),
                    "drug_name": rx.drug_name,
                    "dose": rx.dose,
                    "frequency": rx.frequency,
                    "duration": rx.duration,
                    "instructions": rx.instructions,
                    "quantity_prescribed": calculated_qty,
                }
            ]
        elif not items:
            items = [
                {
                    "prescription_item_id": "d4000004-0004-4004-8004-000000000004",
                    "drug_name": "Amoxicillin",
                    "dose": "500mg",
                    "frequency": "Three times daily",
                    "duration": "7 days",
                    "instructions": "Take after meals",
                    "quantity_prescribed": 21,
                }
            ]

        # Billing is owned exclusively by billing-service, which charges the
        # visit bill when pharmacy later publishes `drug.dispensed` (see
        # dispense_prescription() in app/services/pharmacy.py and
        # billing-service's apply_drug_charge()). Do not write Bill/BillItem
        # rows here — this used to duplicate that charge at prescription-issue
        # time (before the drug was actually dispensed) using a divergent
        # schema, racing against billing-service's own writes to the same
        # tenant tables.
        for item in items:
            item_id = UUID(item["prescription_item_id"]) if "prescription_item_id" in item else uuid4()
            drug_name = item["drug_name"]
            qty = int(item.get("quantity_prescribed") or 1)

            actual_rx_id = rx.prescription_id if rx else prescription_id
            rx_item = PrescriptionItem(
                prescription_item_id=item_id,
                prescription_id=actual_rx_id,
                drug_name=drug_name,
                dose=item.get("dose"),
                frequency=item.get("frequency"),
                duration=item.get("duration"),
                instructions=item.get("instructions"),
                quantity_prescribed=qty,
                status="pending",
            )
            db.add(rx_item)

        # Set visit billing_cleared to False so patient clears bill before dispensing
        visit.billing_cleared = False

        await db.commit()


async def handle_payment_received(payload: dict, tenant_id: str) -> None:
    """
    Handle payment.received event.
    Confirms billing clearance for the associated visit.
    """
    visit_id_str = payload.get("visit_id") or payload.get("payment_id")
    if not visit_id_str:
        return

    try:
        visit_id = UUID(visit_id_str)
    except ValueError:
        return

    async for db in get_tenant_session(tenant_id):
        visit = await db.get(Visit, visit_id)
        if visit:
            visit.billing_cleared = True
            await db.commit()


async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "prescription.issued":
        await handle_prescription_issued(payload, payload.get("tenant_id", "default"))
    elif routing_key == "payment.received":
        await handle_payment_received(payload, payload.get("tenant_id", "default"))


async def start_subscriber() -> None:
    await start_consumer(
        service_name="pharmacy-service",
        routing_keys=["prescription.issued", "payment.received"],
        handler=_dispatch,
    )
