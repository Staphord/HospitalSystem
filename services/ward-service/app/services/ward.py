"""Ward business logic (FR-47–FR-52)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ward import (
    Admission,
    Bed,
    Consultation,
    InpatientOrder,
    NursingNote,
    ShiftHandover,
    Visit,
    VisitorLog,
)

logger = logging.getLogger("ward_service")

ORDER_TYPES = {"medication", "nursing", "diet", "investigation", "procedure", "other"}
ORDER_STATUSES = {"active", "completed", "discontinued"}
NOTE_TYPES = {"observation", "intervention", "progress", "medication_given", "ward_round"}
ADMISSION_ACTIVE = "active"
ADMISSION_DISCHARGED = "discharged"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_los_days(admission_date: datetime, end: datetime | None = None) -> Decimal:
    end = end or _utcnow()
    if admission_date.tzinfo is None:
        admission_date = admission_date.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds = max((end - admission_date).total_seconds(), 0)
    days = Decimal(str(round(seconds / 86400.0, 1)))
    return max(days, Decimal("0.1")) if seconds > 0 else Decimal("0.0")


async def list_beds(
    db: AsyncSession,
    *,
    ward_name: str | None = None,
    bed_type: str | None = None,
    is_available: bool | None = None,
    is_active: bool | None = True,
) -> list[Bed]:
    q = select(Bed)
    if ward_name:
        q = q.where(Bed.ward_name == ward_name)
    if bed_type:
        q = q.where(Bed.bed_type == bed_type)
    if is_available is not None:
        q = q.where(Bed.is_available.is_(is_available))
    if is_active is not None:
        q = q.where(Bed.is_active.is_(is_active))
    q = q.order_by(Bed.ward_name, Bed.bed_number)
    return list((await db.execute(q)).scalars().all())


async def beds_board(db: AsyncSession) -> dict[str, Any]:
    beds = await list_beds(db, is_active=True)
    by_ward: dict[str, list[dict[str, Any]]] = {}
    for b in beds:
        by_ward.setdefault(b.ward_name, []).append(
            {
                "bed_id": str(b.bed_id),
                "bed_number": b.bed_number,
                "bed_type": b.bed_type,
                "is_available": b.is_available,
                "occupied": not b.is_available,
            }
        )
    return {"wards": [{"ward_name": k, "beds": v} for k, v in sorted(by_ward.items())]}


async def _lock_bed(db: AsyncSession, bed_id: uuid.UUID) -> Bed:
    result = await db.execute(
        select(Bed).where(Bed.bed_id == bed_id).with_for_update()
    )
    bed = result.scalars().first()
    if not bed or not bed.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bed not found or inactive")
    return bed


async def assign_bed(
    db: AsyncSession, bed_id: uuid.UUID, *, admission_id: uuid.UUID | None = None
) -> Bed:
    bed = await _lock_bed(db, bed_id)
    if not bed.is_available:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Bed is already occupied")
    bed.is_available = False
    bed.updated_at = _utcnow()
    if admission_id:
        adm = (
            await db.execute(select(Admission).where(Admission.admission_id == admission_id))
        ).scalars().first()
        if not adm:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Admission not found")
        if adm.status != ADMISSION_ACTIVE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Admission is not active")
        # release previous bed if transferring
        if adm.bed_id != bed_id:
            prev = await _lock_bed(db, adm.bed_id)
            prev.is_available = True
            prev.updated_at = _utcnow()
            adm.bed_id = bed_id
            adm.ward_name = bed.ward_name
            adm.updated_at = _utcnow()
    await db.commit()
    await db.refresh(bed)
    return bed


async def release_bed(db: AsyncSession, bed_id: uuid.UUID) -> Bed:
    bed = await _lock_bed(db, bed_id)
    bed.is_available = True
    bed.updated_at = _utcnow()
    await db.commit()
    await db.refresh(bed)
    return bed


async def create_admission(
    db: AsyncSession,
    *,
    visit_id: uuid.UUID,
    bed_id: uuid.UUID,
    admitting_diagnosis: str,
    doctor_sub: str,
    tenant_id: str,
    require_disposition: bool = True,
) -> Admission:
    visit = (await db.execute(select(Visit).where(Visit.visit_id == visit_id))).scalars().first()
    if not visit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Visit not found")

    existing = (
        await db.execute(
            select(Admission).where(
                Admission.visit_id == visit_id,
                Admission.status == ADMISSION_ACTIVE,
            )
        )
    ).scalars().first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Visit already has an active admission")

    # Soften: only enforce disposition when a consultation row exists
    if require_disposition:
        cons = (
            await db.execute(select(Consultation).where(Consultation.visit_id == visit_id))
        ).scalars().first()
        if cons is not None and cons.disposition != "admission":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Consultation disposition must be 'admission' before admitting "
                    f"(current={cons.disposition!r})"
                ),
            )

    bed = await _lock_bed(db, bed_id)
    if not bed.is_available:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Bed is already occupied")

    bed.is_available = False
    bed.updated_at = _utcnow()
    adm = Admission(
        admission_id=uuid.uuid4(),
        visit_id=visit_id,
        patient_id=visit.patient_id,
        bed_id=bed_id,
        admitting_doctor_id=doctor_sub,
        admitting_diagnosis=admitting_diagnosis,
        admission_date=_utcnow(),
        status=ADMISSION_ACTIVE,
        ward_name=bed.ward_name,
    )
    db.add(adm)
    visit.visit_type = "inpatient"
    visit.updated_at = _utcnow()
    await db.commit()
    await db.refresh(adm)
    # Best-effort visit status (enum may lack 'admitted' until migration applied)
    try:
        await db.execute(
            text("UPDATE visits SET status = 'admitted', updated_at = now() WHERE visit_id = :vid"),
            {"vid": str(visit_id)},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Could not set visit status=admitted for %s", visit_id)
    return adm


async def get_admission(db: AsyncSession, admission_id: uuid.UUID) -> Admission:
    adm = (
        await db.execute(select(Admission).where(Admission.admission_id == admission_id))
    ).scalars().first()
    if not adm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Admission not found")
    return adm


async def list_admissions(
    db: AsyncSession,
    *,
    status_filter: str | None = None,
    patient_id: uuid.UUID | None = None,
    ward_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Admission]:
    q = select(Admission)
    if status_filter:
        q = q.where(Admission.status == status_filter)
    if patient_id:
        q = q.where(Admission.patient_id == patient_id)
    if ward_name:
        q = q.where(Admission.ward_name == ward_name)
    q = q.order_by(Admission.admission_date.desc()).limit(limit).offset(offset)
    return list((await db.execute(q)).scalars().all())


async def create_order(
    db: AsyncSession,
    admission_id: uuid.UUID,
    data: dict[str, Any],
    ordered_by: str,
) -> InpatientOrder:
    adm = await get_admission(db, admission_id)
    if adm.status != ADMISSION_ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Admission is not active")
    order_type = data["order_type"]
    if order_type not in ORDER_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid order_type: {order_type}")
    row = InpatientOrder(
        order_id=uuid.uuid4(),
        admission_id=admission_id,
        patient_id=adm.patient_id,
        order_type=order_type,
        order_detail=data["order_detail"],
        frequency=data.get("frequency"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        ordered_by=ordered_by,
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_orders(db: AsyncSession, admission_id: uuid.UUID) -> list[InpatientOrder]:
    await get_admission(db, admission_id)
    q = (
        select(InpatientOrder)
        .where(InpatientOrder.admission_id == admission_id)
        .order_by(InpatientOrder.ordered_at.desc())
    )
    return list((await db.execute(q)).scalars().all())


async def update_order(
    db: AsyncSession, admission_id: uuid.UUID, order_id: uuid.UUID, data: dict[str, Any]
) -> InpatientOrder:
    await get_admission(db, admission_id)
    row = (
        await db.execute(
            select(InpatientOrder).where(
                InpatientOrder.order_id == order_id,
                InpatientOrder.admission_id == admission_id,
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    if "status" in data and data["status"] is not None:
        if data["status"] not in ORDER_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid status")
        row.status = data["status"]
    for key in ("order_detail", "frequency", "start_date", "end_date"):
        if key in data and data[key] is not None:
            setattr(row, key, data[key])
    await db.commit()
    await db.refresh(row)
    return row


async def create_nursing_note(
    db: AsyncSession, admission_id: uuid.UUID, data: dict[str, Any], authored_by: str
) -> NursingNote:
    adm = await get_admission(db, admission_id)
    if adm.status != ADMISSION_ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Admission is not active")
    note_type = data["note_type"]
    if note_type not in NOTE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid note_type: {note_type}")
    row = NursingNote(
        note_id=uuid.uuid4(),
        admission_id=admission_id,
        patient_id=adm.patient_id,
        note_type=note_type,
        note_text=data["note_text"],
        vitals_bp=data.get("vitals_bp"),
        vitals_temp=data.get("vitals_temp"),
        vitals_pulse=data.get("vitals_pulse"),
        vitals_spo2=data.get("vitals_spo2"),
        authored_by=authored_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_nursing_notes(db: AsyncSession, admission_id: uuid.UUID) -> list[NursingNote]:
    await get_admission(db, admission_id)
    q = (
        select(NursingNote)
        .where(NursingNote.admission_id == admission_id)
        .order_by(NursingNote.authored_at.desc())
    )
    return list((await db.execute(q)).scalars().all())


async def discharge_admission(
    db: AsyncSession,
    admission_id: uuid.UUID,
    *,
    discharge_diagnosis: str,
    discharge_instructions: str | None,
    doctor_sub: str,
) -> Admission:
    result = await db.execute(
        select(Admission).where(Admission.admission_id == admission_id).with_for_update()
    )
    adm = result.scalars().first()
    if not adm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Admission not found")
    if adm.status != ADMISSION_ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Admission already discharged")

    now = _utcnow()
    adm.discharge_date = now
    adm.length_of_stay_days = compute_los_days(adm.admission_date, now)
    adm.discharge_diagnosis = discharge_diagnosis
    adm.discharge_instructions = discharge_instructions
    adm.discharge_order_by = doctor_sub
    adm.status = ADMISSION_DISCHARGED
    adm.updated_at = now

    bed = await _lock_bed(db, adm.bed_id)
    bed.is_available = True
    bed.updated_at = now

    await db.commit()
    await db.refresh(adm)
    try:
        await db.execute(
            text("UPDATE visits SET status = 'discharged', updated_at = now() WHERE visit_id = :vid"),
            {"vid": str(adm.visit_id)},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Could not set visit status=discharged for %s", adm.visit_id)
    return adm


async def get_los(db: AsyncSession, admission_id: uuid.UUID) -> dict[str, Any]:
    adm = await get_admission(db, admission_id)
    if adm.status == ADMISSION_DISCHARGED and adm.length_of_stay_days is not None:
        days = Decimal(str(adm.length_of_stay_days))
    else:
        days = compute_los_days(adm.admission_date)
    return {
        "admission_id": str(adm.admission_id),
        "status": adm.status,
        "admission_date": adm.admission_date,
        "discharge_date": adm.discharge_date,
        "length_of_stay_days": float(days),
    }


VISITOR_STATUSES = {"active", "departed", "denied", "overstay"}


def _visitor_time_left(row: VisitorLog, now: datetime | None = None) -> int | None:
    if row.status not in ("active", "overstay") or row.check_out_at is not None:
        return None
    now = now or _utcnow()
    check_in = row.check_in_at
    if check_in.tzinfo is None:
        check_in = check_in.replace(tzinfo=timezone.utc)
    elapsed = int((now - check_in).total_seconds())
    allowed = int(row.allowed_duration_minutes or 30) * 60
    return allowed - elapsed


async def _refresh_overstays(db: AsyncSession) -> None:
    now = _utcnow()
    rows = list(
        (
            await db.execute(
                select(VisitorLog).where(VisitorLog.status == "active", VisitorLog.check_out_at.is_(None))
            )
        ).scalars().all()
    )
    changed = False
    for row in rows:
        left = _visitor_time_left(row, now)
        if left is not None and left <= 0:
            row.status = "overstay"
            changed = True
    if changed:
        await db.commit()


async def list_visitors(
    db: AsyncSession,
    *,
    status: str | None = None,
    active_only: bool = False,
    limit: int = 200,
) -> list[VisitorLog]:
    await _refresh_overstays(db)
    q = select(VisitorLog)
    if active_only:
        q = q.where(VisitorLog.status.in_(("active", "overstay")), VisitorLog.check_out_at.is_(None))
    elif status:
        q = q.where(VisitorLog.status == status.lower())
    q = q.order_by(VisitorLog.check_in_at.desc()).limit(limit)
    return list((await db.execute(q)).scalars().all())


async def create_visitor(
    db: AsyncSession, data: dict[str, Any], *, approved_by: str
) -> VisitorLog:
    admission_id = data.get("admission_id")
    patient_id = None
    if admission_id:
        adm = await get_admission(db, admission_id)
        patient_id = adm.patient_id

    approved = bool(data.get("approved", True))
    status = "active" if approved else "denied"
    row = VisitorLog(
        visitor_id=uuid.uuid4(),
        admission_id=admission_id,
        patient_id=patient_id,
        patient_name=data["patient_name"],
        bed_label=data["bed_label"],
        visitor_name=data["visitor_name"],
        relationship=data["relationship"],
        national_id=data.get("national_id"),
        check_in_at=_utcnow(),
        approved_by=approved_by,
        status=status,
        denial_reason=None if approved else (data.get("denial_reason") or "Denied"),
        allowed_duration_minutes=int(data.get("allowed_duration_minutes") or 30),
        ward_name=data.get("ward_name"),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def checkout_visitor(db: AsyncSession, visitor_id: uuid.UUID) -> VisitorLog:
    row = (
        await db.execute(select(VisitorLog).where(VisitorLog.visitor_id == visitor_id))
    ).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Visitor not found")
    if row.status == "departed":
        return row
    if row.status == "denied":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Denied visitors cannot be checked out")
    row.status = "departed"
    row.check_out_at = _utcnow()
    await db.commit()
    await db.refresh(row)
    return row


async def list_handovers(db: AsyncSession, *, limit: int = 50) -> list[ShiftHandover]:
    q = select(ShiftHandover).order_by(ShiftHandover.created_at.desc()).limit(limit)
    return list((await db.execute(q)).scalars().all())


async def create_handover(
    db: AsyncSession, data: dict[str, Any], *, submitted_by: str
) -> ShiftHandover:
    notes = data.get("patient_notes") or {}
    row = ShiftHandover(
        handover_id=uuid.uuid4(),
        shift_label=data["shift_label"],
        submitted_by=submitted_by,
        overall_summary=data["overall_summary"],
        incidents_summary=data.get("incidents_summary") or "0 Reported",
        patient_count=len(notes) if notes else int(data.get("patient_count") or 0),
        patient_notes=notes,
        ward_name=data.get("ward_name"),
    )
    if row.patient_count == 0 and notes:
        row.patient_count = len(notes)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
