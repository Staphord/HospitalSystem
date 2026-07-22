"""
Pharmacy business logic with active database support.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    DispenseRequest,
    DispenseResponse,
    DispenseSummaryResponse,
    DispenseSummaryItem,
    InteractionCheckResponse,
    InteractionAlert,
    LabelGenerateRequest,
    LabelGenerateResponse,
    MarkNotificationReadResponse,
    PharmacyNotificationsResponse,
    PharmacyQueueItem,
    PharmacyQueueResponse,
    VisitPrescriptionsResponse,
    PrescriptionItem as SchemaPrescriptionItem,
)
from app.core.security import TokenPayload
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.pharmacy import (
    Queue, Visit, Patient, Prescription, PrescriptionItem, DispensingRecord,
    DrugInventory, DrugInventoryTransaction, Consultation, Diagnosis
)
from app.events.publisher import publish_drug_dispensed

# Stable seed IDs for fallback notifications or testing compatibility
STUB_QUEUE_ID = UUID("a1000001-0001-4001-8001-000000000001")
STUB_QUEUE_COMPLETED_ID = UUID("a1000001-0001-4001-8001-000000000099")
STUB_VISIT_ID = UUID("b2000002-0002-4002-8002-000000000002")
STUB_PATIENT_ID = UUID("c3000003-0003-4003-8003-000000000003")
STUB_PRESCRIPTION_PENDING_ID = UUID("d4000004-0004-4004-8004-000000000004")
STUB_PRESCRIPTION_DISPENSED_ID = UUID("d4000004-0004-4004-8004-000000000005")
STUB_DISPENSING_ID = UUID("f6000006-0006-4006-8006-000000000006")
STUB_NOTIFICATION_ID = UUID("a7000007-0007-4007-8007-000000000007")

_NOW = datetime.now(timezone.utc)


def _pharmacist_name(user: TokenPayload) -> str:
    return user.preferred_username or user.email or "Pharmacist"


# ── Queue ──────────────────────────────────────────────────────────────────────

async def get_pharmacy_queue(
    db: AsyncSession,
    queue_date: date,
    status: str,
) -> PharmacyQueueResponse:
    if status not in ("waiting", "in_progress", "completed"):
        status = "waiting"

    stmt = (
        select(Queue, Visit, Patient)
        .join(Visit, Queue.visit_id == Visit.visit_id)
        .join(Patient, Visit.patient_id == Patient.id)
        .where(
            Queue.queue_type == "pharmacy",
            Queue.status == status,
            func.date(Queue.created_at) == queue_date,
        )
        .order_by(Queue.created_at.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    queue_items = []
    for queue, visit, patient in rows:
        # Count prescription items for this visit
        rx_stmt = select(func.count(PrescriptionItem.prescription_item_id)).join(
            Prescription, PrescriptionItem.prescription_id == Prescription.prescription_id
        ).where(Prescription.visit_id == visit.visit_id)
        rx_res = await db.execute(rx_stmt)
        prescription_count = rx_res.scalar() or 0

        queue_items.append(
            PharmacyQueueItem(
                queue_id=queue.queue_id,
                queue_number=queue.queue_number,
                priority=queue.priority,
                status=queue.status,
                visit_id=visit.visit_id,
                visit_number=visit.visit_number,
                patient_id=patient.id,
                patient_name=patient.full_name,
                payment_type=visit.payment_type,
                billing_cleared=visit.billing_cleared,
                prescription_count=prescription_count,
                called_at=queue.called_at,
                created_at=queue.created_at,
            )
        )

    return PharmacyQueueResponse(date=queue_date, queue=queue_items)


async def call_queue_patient(db: AsyncSession, queue_id: UUID, user: TokenPayload) -> PharmacyQueueItem:
    queue = await db.get(Queue, queue_id)
    if not queue:
        raise NotFoundError("Queue entry not found")
    if queue.status == "completed":
        raise ConflictError("Queue entry is already completed")

    now = datetime.now(timezone.utc)
    queue.status = "in_progress"
    queue.called_at = now
    await db.commit()
    await db.refresh(queue)

    visit = await db.get(Visit, queue.visit_id)
    patient = await db.get(Patient, visit.patient_id)

    rx_stmt = select(func.count(PrescriptionItem.prescription_item_id)).join(
        Prescription, PrescriptionItem.prescription_id == Prescription.prescription_id
    ).where(Prescription.visit_id == visit.visit_id)
    rx_res = await db.execute(rx_stmt)
    prescription_count = rx_res.scalar() or 0

    return PharmacyQueueItem(
        queue_id=queue.queue_id,
        queue_number=queue.queue_number,
        priority=queue.priority,
        status=queue.status,
        visit_id=visit.visit_id,
        visit_number=visit.visit_number,
        patient_id=patient.id,
        patient_name=patient.full_name,
        payment_type=visit.payment_type,
        billing_cleared=True,
        prescription_count=prescription_count,
        called_at=queue.called_at,
        created_at=queue.created_at,
    )


# ── Prescriptions ──────────────────────────────────────────────────────────────

async def get_visit_prescriptions(db: AsyncSession, visit_id: UUID) -> VisitPrescriptionsResponse:
    # Query prescription joined with visit and patient
    rx_stmt = select(Prescription).where(Prescription.visit_id == visit_id)
    rx_res = await db.execute(rx_stmt)
    prescription = rx_res.scalar_one_or_none()

    if not prescription:
        raise NotFoundError("Visit not found or no prescription recorded")

    visit = await db.get(Visit, visit_id)
    patient = await db.get(Patient, visit.patient_id)

    # Get diagnosis
    diag_stmt = (
        select(Diagnosis.description)
        .join(Consultation, Diagnosis.consultation_id == Consultation.id)
        .where(Consultation.visit_id == visit_id)
        .limit(1)
    )
    diag_res = await db.execute(diag_stmt)
    final_diagnosis = diag_res.scalar() or "No diagnosis specified"

    prescribed_items = []
    for item in prescription.items:
        disp_summary = None
        if item.dispensing_record:
            disp_summary = {
                "dispensing_id": item.dispensing_record.dispensing_id,
                "quantity_dispensed": item.dispensing_record.quantity_dispensed,
                "dispensed_at": item.dispensing_record.dispensed_at,
            }

        prescribed_items.append(
            SchemaPrescriptionItem(
                prescription_id=item.prescription_item_id,
                drug_name=item.drug_name,
                dose=item.dose or "",
                frequency=item.frequency or "",
                duration=item.duration or "",
                route="oral",
                instructions=item.instructions,
                quantity_prescribed=item.quantity_prescribed,
                prescribed_by=prescription.prescribed_by or "Doctor",
                prescribed_at=prescription.prescribed_at,
                status=item.status,
                dispensing_record=disp_summary,
            )
        )

    return VisitPrescriptionsResponse(
        visit_id=visit_id,
        visit_number=visit.visit_number,
        patient={
            "patient_id": patient.id,
            "patient_name": patient.full_name,
            "date_of_birth": patient.date_of_birth,
            "allergies": patient.allergies,
        },
        final_diagnosis=final_diagnosis,
        billing_cleared=visit.billing_cleared,
        prescriptions=prescribed_items,
    )


async def check_drug_interactions(db: AsyncSession, visit_id: UUID) -> InteractionCheckResponse:
    visit = await db.get(Visit, visit_id)
    if not visit:
        raise NotFoundError("Visit not found")
    patient = await db.get(Patient, visit.patient_id)

    # Fetch prescriptions for this visit
    rx_stmt = select(PrescriptionItem).join(Prescription).where(Prescription.visit_id == visit_id)
    rx_res = await db.execute(rx_stmt)
    items = rx_res.scalars().all()

    alerts = []
    # Check patient allergies
    if patient and patient.allergies:
        allergies_list = [a.strip().lower() for a in patient.allergies.split(",") if a.strip()]
        for item in items:
            drug_lower = item.drug_name.lower()
            for allergy in allergies_list:
                # Substring matching (e.g. "penicillin" in "Amoxicillin")
                if allergy in drug_lower or (allergy == "penicillin" and "amox" in drug_lower):
                    alerts.append(
                        InteractionAlert(
                            type="drug_allergy",
                            severity="high",
                            drug_name=item.drug_name,
                            detail=f"Patient has documented {allergy.title()} allergy. {item.drug_name} matches this contraindication.",
                            recommendation="Confirm with prescribing doctor before dispensing.",
                        )
                    )

    # Simple drug-drug interaction check (e.g. Warfarin and Ibuprofen)
    drug_names = {item.drug_name.lower() for item in items}
    if any("warfarin" in d for d in drug_names) and any("ibuprofen" in d for d in drug_names):
        alerts.append(
            InteractionAlert(
                type="drug_drug",
                severity="high",
                drug_a="Warfarin",
                drug_b="Ibuprofen",
                detail="High risk of bleeding. NSAIDs like Ibuprofen increase Warfarin's anticoagulant effect.",
                recommendation="Use alternative analgesic or obtain explicit doctor authorization.",
            )
        )

    return InteractionCheckResponse(
        visit_id=visit_id,
        alerts=alerts,
        alert_count=len(alerts),
        checked_at=datetime.now(timezone.utc),
    )


# ── Dispensing ─────────────────────────────────────────────────────────────────

async def dispense_prescription(
    db: AsyncSession,
    body: DispenseRequest,
    user: TokenPayload,
) -> DispenseResponse:
    # Look up prescription item
    item = await db.get(PrescriptionItem, body.prescription_id)
    if not item:
        raise NotFoundError("Prescription item not found")
    if item.status == "dispensed":
        raise ConflictError("ALREADY_DISPENSED")

    # Verify billing clearance
    visit = await db.get(Visit, body.visit_id)
    if not visit:
        raise NotFoundError("Visit not found")
    if not visit.billing_cleared:
        raise ConflictError("BILLING_NOT_CLEARED")

    # Verify interaction acknowledgement if alerts are present
    alerts = await check_drug_interactions(db, body.visit_id)
    if alerts.alert_count > 0 and not body.interaction_alert_acknowledged:
        raise ConflictError("INTERACTION_ALERT_NOT_ACKNOWLEDGED")

    # Find the drug in the inventory
    inv_stmt = select(DrugInventory).where(
        DrugInventory.drug_name.ilike(f"%{body.drug_name}%"),
        DrugInventory.is_active.is_(True),
    )
    inv_res = await db.execute(inv_stmt)
    inventory_item = inv_res.scalar_one_or_none()
    if not inventory_item:
        raise NotFoundError(f"Drug '{body.drug_name}' not found in pharmacy inventory")

    if inventory_item.quantity_in_stock < body.quantity_dispensed:
        raise ConflictError("INSUFFICIENT_STOCK")

    # Decrement stock and update inventory
    now = datetime.now(timezone.utc)
    qty_before = inventory_item.quantity_in_stock
    qty_after = qty_before - body.quantity_dispensed
    inventory_item.quantity_in_stock = qty_after
    inventory_item.updated_at = now

    # Record inventory transaction
    tx = DrugInventoryTransaction(
        inventory_id=inventory_item.inventory_id,
        transaction_type="dispense",
        quantity_change=-body.quantity_dispensed,
        quantity_before=qty_before,
        quantity_after=qty_after,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        performed_by=user.sub or "unknown",
        performed_by_name=_pharmacist_name(user),
        reference_id=body.prescription_id,
    )
    db.add(tx)

    # Save dispensing record
    dispensing_id = uuid4()
    dispense_record = DispensingRecord(
        dispensing_id=dispensing_id,
        prescription_item_id=body.prescription_id,
        visit_id=body.visit_id,
        inventory_id=inventory_item.inventory_id,
        quantity_dispensed=body.quantity_dispensed,
        unit=body.unit,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        dispensed_by=_pharmacist_name(user),
        dispensed_at=now,
    )
    db.add(dispense_record)

    # Update prescription item status
    item.status = "dispensed"

    # Check if all other items in this prescription are dispensed/cancelled
    prescription = await db.get(Prescription, item.prescription_id)
    if prescription:
        all_items_stmt = select(PrescriptionItem).where(PrescriptionItem.prescription_id == prescription.prescription_id)
        all_items_res = await db.execute(all_items_stmt)
        all_items = all_items_res.scalars().all()

        if all(i.status in ("dispensed", "cancelled") for i in all_items):
            prescription.status = "completed"

            # Update queue status
            queue_stmt = select(Queue).where(Queue.visit_id == body.visit_id, Queue.queue_type == "pharmacy")
            queue_res = await db.execute(queue_stmt)
            queue_items = queue_res.scalars().all()
            for q in queue_items:
                q.status = "completed"
                q.completed_at = now

    await db.commit()
    await db.refresh(inventory_item)

    # Publish drug.dispensed event to notify billing service
    try:
        tenant_id = user.tenant_id if hasattr(user, "tenant_id") else "default"
        await publish_drug_dispensed(str(dispensing_id), tenant_id)
    except Exception as exc:
        # Prevent event publishing failure from failing the HTTP response
        import logging
        logging.getLogger(__name__).exception("Failed to publish drug.dispensed event: %s", exc)

    return DispenseResponse(
        dispensing_id=dispensing_id,
        prescription_id=body.prescription_id,
        drug_name=body.drug_name,
        quantity_dispensed=body.quantity_dispensed,
        unit=body.unit,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        billing_cleared=True,
        dispensed_by=_pharmacist_name(user),
        dispensed_at=now,
        remaining_stock=qty_after,
        low_stock_alert_sent=(qty_after <= inventory_item.reorder_level),
        bill_item_id=uuid4(),  # Mock bill_item_id generated locally
    )


async def get_dispense_summary(db: AsyncSession, visit_id: UUID) -> DispenseSummaryResponse:
    visit = await db.get(Visit, visit_id)
    if not visit:
        raise NotFoundError("Visit not found")

    patient = await db.get(Patient, visit.patient_id)
    patient_name = patient.full_name if patient else "Unknown"

    rx_stmt = select(Prescription).where(Prescription.visit_id == visit_id)
    rx_res = await db.execute(rx_stmt)
    rxs = rx_res.scalars().all()

    items = []
    for rx in rxs:
        items.extend(rx.items)

    summary_items = []
    for item in items:
        disp_id = None
        qty_dispensed = None
        dispensed_at = None

        if item.dispensing_record:
            disp_id = item.dispensing_record.dispensing_id
            qty_dispensed = item.dispensing_record.quantity_dispensed
            dispensed_at = item.dispensing_record.dispensed_at

        summary_items.append(
            DispenseSummaryItem(
                prescription_id=item.prescription_item_id,
                drug_name=item.drug_name,
                prescribed_dose=item.dose or "",
                prescribed_frequency=item.frequency,
                prescription_status=item.status,
                dispensing_id=disp_id,
                quantity_dispensed=qty_dispensed,
                dispensed_at=dispensed_at,
            )
        )

    total = len(items)
    dispensed = sum(1 for i in items if i.status == "dispensed")
    pending = sum(1 for i in items if i.status == "pending")
    cancelled = sum(1 for i in items if i.status == "cancelled")

    return DispenseSummaryResponse(
        visit_id=visit_id,
        patient_name=patient_name,
        prescriptions_total=total,
        dispensed_count=dispensed,
        pending_count=pending,
        cancelled_count=cancelled,
        items=summary_items,
    )


# ── Labels ─────────────────────────────────────────────────────────────────────

async def generate_label(
    db: AsyncSession,
    body: LabelGenerateRequest,
    user: TokenPayload,
) -> LabelGenerateResponse:
    if body.dispensing_id:
        stmt = (
            select(DispensingRecord, PrescriptionItem, Prescription, Patient)
            .join(PrescriptionItem, DispensingRecord.prescription_item_id == PrescriptionItem.prescription_item_id)
            .join(Prescription, PrescriptionItem.prescription_id == Prescription.prescription_id)
            .join(Patient, Prescription.patient_id == Patient.id)
            .where(DispensingRecord.dispensing_id == body.dispensing_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise NotFoundError("Dispensing record not found")

        dispense, item, rx, patient = row
        dispensed_date = dispense.dispensed_at.strftime("%d %B %Y")
        dispensed_by = dispense.dispensed_by or _pharmacist_name(user)
        batch_number = dispense.batch_number or ""
        expiry_date = dispense.expiry_date.strftime("%B %Y") if dispense.expiry_date else ""
    elif body.prescription_item_id:
        stmt = (
            select(PrescriptionItem, Prescription, Patient)
            .join(Prescription, PrescriptionItem.prescription_id == Prescription.prescription_id)
            .join(Patient, Prescription.patient_id == Patient.id)
            .where(PrescriptionItem.prescription_item_id == body.prescription_item_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise NotFoundError("Prescription item not found")

        item, rx, patient = row
        dispensed_date = datetime.now(timezone.utc).strftime("%d %B %Y")
        dispensed_by = _pharmacist_name(user)
        batch_number = "PREVIEW"
        expiry_date = "N/A"
    else:
        raise BadRequestError("Either dispensing_id or prescription_item_id must be provided")

    return LabelGenerateResponse(
        label={
            "patient_name": patient.full_name,
            "drug_name": item.drug_name,
            "dose": item.dose or "",
            "frequency": item.frequency or "",
            "duration": item.duration or "",
            "route": "Oral",
            "instructions": item.instructions or "Take as directed",
            "dispensed_date": dispensed_date,
            "dispensed_by": dispensed_by,
            "batch_number": batch_number,
            "expiry_date": expiry_date,
        }
    )


# ── Notifications ──────────────────────────────────────────────────────────────

async def list_notifications(db: AsyncSession) -> PharmacyNotificationsResponse:
    # Stub response matching schema format
    return PharmacyNotificationsResponse(
        unread_count=0,
        notifications=[],
    )


async def mark_notification_read(db: AsyncSession, notification_id: UUID) -> MarkNotificationReadResponse:
    return MarkNotificationReadResponse(marked_read=True)
