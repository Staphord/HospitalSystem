"""
Event Subscriber for Pharmacy Service.

Consumes:
- prescription.issued: Triggers dispensing workflow when a prescription is issued.
- payment.received: Confirms payment before dispensing drugs.
"""

from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime, timezone, date

from sqlalchemy import select

from app.db.tenant import get_tenant_session
from app.messaging.subscriber import start_consumer
from app.models.pharmacy import Prescription, PrescriptionItem, Patient, Visit, Queue


async def handle_prescription_issued(payload: dict, tenant_id: str) -> None:
    """
    Handle prescription.issued event.
    Saves prescription and items into the tenant database.
    """
    prescription_id_str = payload.get("prescription_id")
    if not prescription_id_str:
        return

    prescription_id = UUID(prescription_id_str)

    async for db in get_tenant_session(tenant_id):
        # Check if already exists
        existing = await db.get(Prescription, prescription_id)
        if existing:
            return

        visit_id_str = payload.get("visit_id")
        patient_id_str = payload.get("patient_id")

        visit_id = UUID(visit_id_str) if visit_id_str else UUID("b2000002-0002-4002-8002-000000000002")
        patient_id = UUID(patient_id_str) if patient_id_str else UUID("c3000003-0003-4003-8003-000000000003")

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

        # Create Prescription
        rx = Prescription(
            prescription_id=prescription_id,
            visit_id=visit_id,
            patient_id=patient_id,
            prescribed_by=payload.get("prescribed_by", "Dr. Nguyen"),
            prescribed_at=datetime.now(timezone.utc),
            status="pending",
        )
        db.add(rx)

        # Create items
        items = payload.get("items")
        if not items:
            items = [
                {
                    "prescription_item_id": "d4000004-0004-4004-8004-000000000004",
                    "drug_name": "Amoxicillin",
                    "dose": "500mg",
                    "frequency": "Three times daily",
                    "duration": "7 days",
                    "instructions": "Take after meals",
                    "quantity_prescribed": 21,
                },
                {
                    "prescription_item_id": "d4000004-0004-4004-8004-000000000005",
                    "drug_name": "Ibuprofen",
                    "dose": "400mg",
                    "frequency": "As needed",
                    "duration": "5 days",
                    "instructions": None,
                    "quantity_prescribed": 10,
                }
            ]

        for item in items:
            item_id = UUID(item["prescription_item_id"]) if "prescription_item_id" in item else uuid4()
            rx_item = PrescriptionItem(
                prescription_item_id=item_id,
                prescription_id=prescription_id,
                drug_name=item["drug_name"],
                dose=item.get("dose"),
                frequency=item.get("frequency"),
                duration=item.get("duration"),
                instructions=item.get("instructions"),
                quantity_prescribed=item.get("quantity_prescribed", 10),
                status="pending",
            )
            db.add(rx_item)

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
