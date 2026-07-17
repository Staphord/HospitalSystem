import logging
from datetime import datetime, timezone, date
from uuid import UUID
from typing import Any, Optional

from sqlalchemy import select, case, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.schemas import (
    ResultCreateRequest,
    ResultUpdateRequest,
    SpecimenCreateRequest,
)
from app.core.security import TokenPayload
from app.exceptions import ConflictError, NotFoundError
from app.models.laboratory import (
    Specimen,
    LabResult,
    InvestigationRequest,
    Queue,
    Patient,
    Visit,
)

logger = logging.getLogger("service.laboratory")


def _user_identifier(user: TokenPayload) -> str:
    return user.preferred_username or user.email or str(user.sub)


# ── Queue & Worklist ────────────────────────────────────────────────────────────

async def get_lab_queue(
    db: AsyncSession,
    status: str,
    queue_date: date,
) -> list[dict]:
    # Urgency sorting weight: stat (1) -> urgent (2) -> routine (3) -> other (4)
    urgency_case = case(
        (InvestigationRequest.urgency == "stat", 1),
        (InvestigationRequest.urgency == "urgent", 2),
        (InvestigationRequest.urgency == "routine", 3),
        else_=4
    )

    # Note: queues table may use string or date type for created_at. We filter by status
    stmt = (
        select(Queue, InvestigationRequest, Patient)
        .join(
            InvestigationRequest,
            and_(
                Queue.visit_id == InvestigationRequest.visit_id,
                InvestigationRequest.request_type == "laboratory",
                InvestigationRequest.status != "completed",
                InvestigationRequest.status != "cancelled"
            )
        )
        .join(Patient, Queue.patient_id == Patient.id)
        .where(
            and_(
                Queue.queue_type == "lab",
                Queue.status == status
            )
        )
        .order_by(urgency_case, Queue.created_at.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for queue_item, req, pat in rows:
        items.append({
            "queue_id": queue_item.queue_id,
            "request_id": req.id,
            "patient_id": pat.id,
            "patient_name": pat.full_name,
            "test_name": req.test_name,
            "urgency": req.urgency or "routine",
            "status": queue_item.status,
            "requested_at": req.created_at,
            "called_at": queue_item.called_at,
            "completed_at": queue_item.completed_at,
        })
    return items

async def get_queue_item_by_id(db: AsyncSession, queue_id: UUID) -> Optional[dict]:
    stmt = (
        select(Queue, InvestigationRequest, Patient)
        .join(
            InvestigationRequest,
            and_(
                Queue.visit_id == InvestigationRequest.visit_id,
                InvestigationRequest.request_type == "laboratory"
            )
        )
        .join(Patient, Queue.patient_id == Patient.id)
        .where(Queue.queue_id == queue_id)
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        return None
    q, req, pat = row
    return {
        "queue_id": q.queue_id,
        "request_id": req.id,
        "patient_id": pat.id,
        "patient_name": pat.full_name,
        "test_name": req.test_name,
        "urgency": req.urgency or "routine",
        "status": q.status,
        "requested_at": req.created_at,
        "called_at": q.called_at,
        "completed_at": q.completed_at,
    }


async def call_queue_patient(db: AsyncSession, queue_id: UUID, user: TokenPayload) -> Queue:
    stmt = select(Queue).where(Queue.queue_id == queue_id)
    res = await db.execute(stmt)
    item = res.scalar_one_or_none()
    if not item:
        raise NotFoundError("Queue entry not found")
    if item.status != "waiting":
        raise ConflictError("Queue entry is already in progress or completed")

    item.status = "in_progress"
    item.called_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return item


async def skip_queue_patient(db: AsyncSession, queue_id: UUID, user: TokenPayload) -> Queue:
    stmt = select(Queue).where(Queue.queue_id == queue_id)
    res = await db.execute(stmt)
    item = res.scalar_one_or_none()
    if not item:
        raise NotFoundError("Queue entry not found")

    item.status = "skipped"
    await db.commit()
    await db.refresh(item)
    return item


# ── Request Details ─────────────────────────────────────────────────────────────

async def get_request_detail(db: AsyncSession, request_id: UUID) -> dict:
    # Query investigation request and join visit and patient
    stmt = (
        select(InvestigationRequest, Patient, Visit)
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .join(Visit, InvestigationRequest.visit_id == Visit.visit_id)
        .where(InvestigationRequest.id == request_id)
    )
    res = await db.execute(stmt)
    row = res.one_or_none()
    if not row:
        raise NotFoundError("Investigation request not found")

    req, pat, vis = row
    if req.request_type != "laboratory":
        raise ConflictError("Requested investigation is not a laboratory test")

    return {
        "request_id": req.id,
        "test_name": req.test_name,
        "request_type": req.request_type,
        "clinical_indication": req.clinical_history,
        "urgency": req.urgency or "routine",
        "requested_by": req.created_by,
        "requested_at": req.created_at,
        "status": req.status,
        "patient": {
            "patient_id": pat.id,
            "patient_number": pat.patient_number or "P-000",
            "full_name": pat.full_name,
            "date_of_birth": pat.date_of_birth,
            "gender": pat.gender,
            "phone": pat.phone_primary,
            "email": pat.email,
            "address": pat.address,
            "allergies": pat.allergies,
        },
        "visit": {
            "visit_id": vis.visit_id,
            "visit_number": vis.visit_number,
            "visit_date": vis.visit_date or date.today(),
            "visit_type": vis.visit_type or "outpatient",
            "payment_type": vis.payment_type or "cash",
        },
    }


# ── Specimen Management ────────────────────────────────────────────────────────

async def collect_specimen(
    db: AsyncSession,
    request_id: UUID,
    body: SpecimenCreateRequest,
    user: TokenPayload,
) -> Specimen:
    # 1. Verify investigation request
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    res = await db.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise NotFoundError("Investigation request not found")
    if req.status != "pending":
        raise ConflictError(f"Cannot collect specimen for request in status '{req.status}'")

    # 2. Create Specimen record
    specimen = Specimen(
        request_id=request_id,
        patient_id=req.patient_id,
        specimen_type=body.specimen_type,
        collection_site=body.collection_site,
        collected_by=_user_identifier(user),
        collected_at=datetime.now(timezone.utc),
        status="collected",
    )
    db.add(specimen)

    # 3. Update parent request status
    req.status = "specimen_collected"
    await db.commit()
    await db.refresh(specimen)
    return specimen


async def receive_specimen(db: AsyncSession, specimen_id: UUID, user: TokenPayload) -> Specimen:
    stmt = select(Specimen).where(Specimen.specimen_id == specimen_id)
    res = await db.execute(stmt)
    specimen = res.scalar_one_or_none()
    if not specimen:
        raise NotFoundError("Specimen record not found")

    specimen.status = "received"
    specimen.received_at = datetime.now(timezone.utc)
    specimen.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(specimen)
    return specimen


async def update_specimen_status(
    db: AsyncSession,
    specimen_id: UUID,
    status: str,
    user: TokenPayload,
) -> Specimen:
    stmt = select(Specimen).where(Specimen.specimen_id == specimen_id)
    res = await db.execute(stmt)
    specimen = res.scalar_one_or_none()
    if not specimen:
        raise NotFoundError("Specimen record not found")

    specimen.status = status
    specimen.updated_at = datetime.now(timezone.utc)

    # Side effect: when set to processing, update parent request status
    if status == "processing":
        stmt_req = select(InvestigationRequest).where(InvestigationRequest.id == specimen.request_id)
        res_req = await db.execute(stmt_req)
        req = res_req.scalar_one_or_none()
        if req:
            req.status = "in_progress"

    await db.commit()
    await db.refresh(specimen)
    return specimen


async def reject_specimen(
    db: AsyncSession,
    specimen_id: UUID,
    rejection_reason: str,
    user: TokenPayload,
) -> Specimen:
    stmt = select(Specimen).where(Specimen.specimen_id == specimen_id)
    res = await db.execute(stmt)
    specimen = res.scalar_one_or_none()
    if not specimen:
        raise NotFoundError("Specimen record not found")

    specimen.status = "rejected"
    specimen.rejection_reason = rejection_reason
    specimen.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(specimen)
    return specimen


# ── Results Entry & Verification ───────────────────────────────────────────────

async def create_lab_result(
    db: AsyncSession,
    request_id: UUID,
    body: ResultCreateRequest,
    user: TokenPayload,
) -> LabResult:
    # 1. Fetch InvestigationRequest details
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    res = await db.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise NotFoundError("Investigation request not found")

    # 2. Check if a result already exists to avoid duplicates
    stmt_check = select(LabResult).where(LabResult.request_id == request_id)
    res_check = await db.execute(stmt_check)
    existing_result = res_check.scalar_one_or_none()
    if existing_result:
        raise ConflictError("A result already exists for this investigation request")

    # 3. Create LabResult
    now_dt = datetime.now(timezone.utc)
    result = LabResult(
        request_id=request_id,
        visit_id=req.visit_id,
        patient_id=req.patient_id,
        specimen_type=body.specimen_type,
        result_value=body.result_value,
        unit=body.unit,
        reference_range=body.reference_range,
        is_critical=body.is_critical,
        result_notes=body.result_notes,
        performed_by=_user_identifier(user),
        status="resulted",
        resulted_at=now_dt,
    )

    # 4. Sync status changes (fire side effects)
    req.status = "completed"

    # Resolve queue item and complete it
    stmt_queue = select(Queue).where(
        and_(
            Queue.visit_id == req.visit_id,
            Queue.queue_type == "lab"
        )
    )
    res_queue = await db.execute(stmt_queue)
    queue_item = res_queue.scalar_one_or_none()
    if queue_item:
        queue_item.status = "completed"
        queue_item.completed_at = now_dt

    # Handle Critical Alert Side Effect (FR-25)
    if body.is_critical:
        # Stage/mock the notifications table write (since notifications table does not exist)
        logger.warning(
            "CRITICAL RESULT ALERT: Notification generated for doctor '%s'. "
            "Critical lab result ID: %s. Patient ID: %s. Value: %s",
            req.created_by, result.result_id, req.patient_id, body.result_value
        )
        result.critical_notified_at = now_dt

    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result


async def update_lab_result(
    db: AsyncSession,
    result_id: UUID,
    body: ResultUpdateRequest,
    user: TokenPayload,
) -> LabResult:
    stmt = select(LabResult).where(LabResult.result_id == result_id)
    res = await db.execute(stmt)
    result = res.scalar_one_or_none()
    if not result:
        raise NotFoundError("Lab result record not found")

    if result.status == "verified":
        raise ConflictError("Cannot modify a verified lab result")

    result.result_value = body.result_value
    result.unit = body.unit
    result.reference_range = body.reference_range
    result.result_notes = body.result_notes
    result.updated_at = datetime.now(timezone.utc)

    # Re-run critical alert logic on transition from normal to critical
    if body.is_critical and not result.is_critical:
        result.is_critical = True
        if not result.critical_notified_at:
            logger.warning(
                "CRITICAL RESULT ALERT (EDITED): Critical lab result ID: %s. Value: %s",
                result.result_id, body.result_value
            )
            result.critical_notified_at = datetime.now(timezone.utc)
    elif not body.is_critical:
        result.is_critical = False

    await db.commit()
    await db.refresh(result)
    return result


async def get_lab_result(db: AsyncSession, result_id: UUID) -> LabResult:
    stmt = select(LabResult).where(LabResult.result_id == result_id)
    res = await db.execute(stmt)
    result = res.scalar_one_or_none()
    if not result:
        raise NotFoundError("Lab result not found")
    return result


async def verify_lab_result(db: AsyncSession, result_id: UUID, user: TokenPayload) -> LabResult:
    stmt = select(LabResult).where(LabResult.result_id == result_id)
    res = await db.execute(stmt)
    result = res.scalar_one_or_none()
    if not result:
        raise NotFoundError("Lab result record not found")

    if result.status != "resulted":
        raise ConflictError(f"Cannot verify result in status '{result.status}'")

    result.status = "verified"
    result.verified_by = _user_identifier(user)
    result.verified_at = datetime.now(timezone.utc)
    result.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(result)
    return result


# ── Patient Results History ───────────────────────────────────────────────────

async def get_patient_results(db: AsyncSession, patient_id: UUID) -> list[LabResult]:
    stmt = (
        select(LabResult)
        .where(LabResult.patient_id == patient_id)
        .order_by(LabResult.resulted_at.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())
