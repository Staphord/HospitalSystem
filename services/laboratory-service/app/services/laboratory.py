import logging
from datetime import datetime, timezone, date
from uuid import UUID
from typing import Optional, Any

from sqlalchemy import select, case, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    SpecimenCreateRequest,
    SpecimenStatusUpdateRequest,
    ResultCreateRequest,
    ResultUpdateRequest,
    LabBillCreateRequest,
)
from app.core.security import TokenPayload
from app.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from app.models.laboratory import (
    Specimen,
    LabResult,
    InvestigationRequest,
    Patient,
    Visit,
    Bill,
    BillItem,
)
from app.models.user import User

logger = logging.getLogger("service.laboratory")


def _user_identifier(user: TokenPayload) -> str:
    return user.preferred_username or user.email or str(user.sub)


async def _resolve_user_name(db: AsyncSession, identifier: Optional[str]) -> Optional[str]:
    if not identifier:
        return None
    # Try querying users table by keycloak_sub or username
    stmt = select(User).where(
        (User.keycloak_sub == identifier) | (User.username == identifier)
    )
    res = await db.execute(stmt)
    u = res.scalar_one_or_none()
    if u and u.full_name:
        return u.full_name
    return identifier


# ── Group 1: Request Queue ───────────────────────────────────────────────────

async def get_lab_requests(
    db: AsyncSession,
    status: Optional[str] = None,
    urgency: Optional[str] = None,
    date_filter: Optional[date] = None,
) -> list[dict]:
    urgency_case = case(
        (InvestigationRequest.urgency == "stat", 1),
        (InvestigationRequest.urgency == "urgent", 2),
        else_=3
    )

    query = (
        select(InvestigationRequest, Patient, User)
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .outerjoin(User, InvestigationRequest.requested_by == User.keycloak_sub)
        .where(InvestigationRequest.request_type.in_(["lab", "laboratory"]))
    )

    if status:
        query = query.where(InvestigationRequest.status == status)

    if urgency:
        query = query.where(InvestigationRequest.urgency == urgency)

    if date_filter:
        query = query.where(
            func.date(InvestigationRequest.requested_at) == date_filter
        )

    query = query.order_by(urgency_case, InvestigationRequest.requested_at.asc())

    res = await db.execute(query)
    rows = res.all()

    requests_list = []
    for req, pat, u in rows:
        requested_by_name = u.full_name if u else (req.requested_by or req.created_by)
        requests_list.append({
            "request_id": req.id,
            "visit_id": req.visit_id,
            "patient_id": pat.id,
            "patient_name": pat.full_name,
            "patient_number": pat.patient_number or "P-000",
            "test_name": req.test_name,
            "test_code": req.test_code,
            "clinical_indication": req.clinical_history,
            "urgency": req.urgency or "routine",
            "status": req.status,
            "requested_by_name": requested_by_name,
            "requested_at": req.requested_at or req.created_at,
        })

    return requests_list


async def get_lab_request_detail(db: AsyncSession, request_id: UUID) -> dict:
    stmt = (
        select(InvestigationRequest, Patient)
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .where(
            and_(
                InvestigationRequest.id == request_id,
                InvestigationRequest.request_type.in_(["lab", "laboratory"])
            )
        )
    )
    res = await db.execute(stmt)
    row = res.one_or_none()
    if not row:
        raise NotFoundError("Investigation request not found or not a lab request")

    req, pat = row

    # Resolve requested_by_name
    requested_by_name = await _resolve_user_name(db, req.requested_by or req.created_by)

    # Active specimen (non-rejected)
    spec_stmt = (
        select(Specimen)
        .where(
            and_(
                Specimen.request_id == request_id,
                Specimen.status != "rejected"
            )
        )
        .order_by(Specimen.collected_at.desc())
    )
    spec_res = await db.execute(spec_stmt)
    spec = spec_res.scalars().first()

    specimen_data = None
    if spec:
        specimen_data = {
            "specimen_id": spec.specimen_id,
            "status": spec.status,
            "specimen_type": spec.specimen_type,
            "collected_at": spec.collected_at,
            "received_at": spec.received_at,
            "rejection_reason": spec.rejection_reason,
        }

    # Result
    res_stmt = select(LabResult).where(LabResult.request_id == request_id)
    res_res = await db.execute(res_stmt)
    lr = res_res.scalar_one_or_none()

    result_data = None
    if lr:
        result_data = {
            "result_id": lr.result_id,
            "status": lr.status,
            "result_value": lr.result_value,
            "unit": lr.unit,
            "reference_range": lr.reference_range,
            "is_critical": lr.is_critical,
            "resulted_at": lr.resulted_at,
        }

    return {
        "request_id": req.id,
        "visit_id": req.visit_id,
        "patient": {
            "patient_id": pat.id,
            "patient_number": pat.patient_number or "P-000",
            "full_name": pat.full_name,
            "date_of_birth": pat.date_of_birth,
            "gender": pat.gender,
        },
        "test_name": req.test_name,
        "test_code": req.test_code,
        "clinical_indication": req.clinical_history,
        "urgency": req.urgency or "routine",
        "status": req.status,
        "requested_by_name": requested_by_name,
        "requested_at": req.requested_at or req.created_at,
        "specimen": specimen_data,
        "result": result_data,
    }


# ── Group 2: Specimen Tracking ───────────────────────────────────────────────

async def collect_specimen(
    db: AsyncSession,
    request_id: UUID,
    body: SpecimenCreateRequest,
    user: TokenPayload,
) -> Specimen:
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    res = await db.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise NotFoundError("Investigation request not found")

    if req.status != "pending":
        raise UnprocessableEntityError(f"Cannot collect specimen for request in status '{req.status}'. Must be 'pending'.")

    # Check active specimen existence
    spec_check = select(Specimen).where(
        and_(
            Specimen.request_id == request_id,
            Specimen.status != "rejected"
        )
    )
    existing_spec = (await db.execute(spec_check)).scalar_one_or_none()
    if existing_spec:
        raise ConflictError("An active specimen already exists for this request")

    specimen = Specimen(
        request_id=request_id,
        patient_id=req.patient_id,
        specimen_type=body.specimen_type,
        collection_site=body.collection_site,
        specimen_label=body.specimen_label,
        collected_by=_user_identifier(user),
        collected_at=body.collected_at,
        status="collected",
    )
    db.add(specimen)

    req.status = "specimen_collected"
    await db.commit()
    await db.refresh(specimen)
    return specimen


async def update_specimen_status(
    db: AsyncSession,
    request_id: UUID,
    body: SpecimenStatusUpdateRequest,
    user: TokenPayload,
) -> dict:
    stmt = select(Specimen).where(
        and_(
            Specimen.request_id == request_id,
            Specimen.status != "rejected"
        )
    )
    res = await db.execute(stmt)
    specimen = res.scalar_one_or_none()
    if not specimen:
        raise NotFoundError("No active specimen found for this request")

    req_stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    req = (await db.execute(req_stmt)).scalar_one_or_none()

    new_status = body.status

    if new_status == "received":
        specimen.status = "received"
        specimen.received_at = body.received_at or datetime.now(timezone.utc)
    elif new_status == "processing":
        specimen.status = "processing"
    elif new_status == "completed":
        specimen.status = "completed"
    elif new_status == "rejected":
        if not body.rejection_reason or not body.rejection_reason.strip():
            raise UnprocessableEntityError("rejection_reason is required when rejecting a specimen")
        specimen.status = "rejected"
        specimen.rejection_reason = body.rejection_reason
        if req:
            req.status = "pending"

    specimen.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(specimen)

    request_status = req.status if req else "pending"
    return {
        "specimen_id": specimen.specimen_id,
        "status": specimen.status,
        "rejection_reason": specimen.rejection_reason,
        "request_status": request_status,
    }


async def get_specimens_for_request(db: AsyncSession, request_id: UUID) -> list[dict]:
    stmt = (
        select(Specimen)
        .where(Specimen.request_id == request_id)
        .order_by(Specimen.collected_at.asc())
    )
    res = await db.execute(stmt)
    specimens = res.scalars().all()

    items = []
    for s in specimens:
        collector_name = await _resolve_user_name(db, s.collected_by)
        items.append({
            "specimen_id": s.specimen_id,
            "specimen_type": s.specimen_type,
            "collection_site": s.collection_site,
            "specimen_label": s.specimen_label,
            "collected_by_name": collector_name,
            "collected_at": s.collected_at,
            "received_at": s.received_at,
            "status": s.status,
            "rejection_reason": s.rejection_reason,
        })
    return items


async def get_all_tracked_specimens(db: AsyncSession) -> list[dict]:
    stmt = (
        select(Specimen, InvestigationRequest, Patient)
        .join(InvestigationRequest, Specimen.request_id == InvestigationRequest.id)
        .join(Patient, Specimen.patient_id == Patient.id)
        .order_by(Specimen.collected_at.desc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    items = []
    for spec, req, pat in rows:
        collector_name = await _resolve_user_name(db, spec.collected_by)
        items.append({
            "specimen_id": spec.specimen_id,
            "request_id": req.id,
            "patient_id": pat.id,
            "patient_name": pat.full_name,
            "patient_number": pat.patient_number or "P-000",
            "test_name": req.test_name,
            "urgency": req.urgency or "routine",
            "specimen_type": spec.specimen_type,
            "collection_site": spec.collection_site,
            "specimen_label": spec.specimen_label,
            "collected_by_name": collector_name,
            "collected_at": spec.collected_at,
            "received_at": spec.received_at,
            "status": spec.status,
            "rejection_reason": spec.rejection_reason,
        })
    return items



# ── Group 3: Results Entry ───────────────────────────────────────────────────

async def create_lab_result(
    db: AsyncSession,
    request_id: UUID,
    body: ResultCreateRequest,
    user: TokenPayload,
) -> LabResult:
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    req = (await db.execute(stmt)).scalar_one_or_none()
    if not req:
        raise NotFoundError("Investigation request not found")

    if req.status not in ["specimen_collected", "in_progress"]:
        raise UnprocessableEntityError(f"Cannot enter result for request in status '{req.status}'. Must be 'specimen_collected' or 'in_progress'.")

    # Check if result already exists
    res_check = select(LabResult).where(LabResult.request_id == request_id)
    existing_res = (await db.execute(res_check)).scalar_one_or_none()
    if existing_res:
        raise ConflictError("A result already exists for this investigation request")

    now_dt = datetime.now(timezone.utc)
    critical_notified = now_dt if body.is_critical else None

    result = LabResult(
        request_id=request_id,
        visit_id=req.visit_id,
        patient_id=req.patient_id,
        specimen_type=body.specimen_type or "unspecified",
        specimen_label=body.specimen_label,
        result_value=body.result_value,
        unit=body.unit,
        reference_range=body.reference_range,
        is_critical=body.is_critical,
        result_notes=body.result_notes,
        performed_by=_user_identifier(user),
        status="resulted",
        resulted_at=now_dt,
        critical_notified_at=critical_notified,
    )

    req.status = "in_progress"

    if body.is_critical:
        logger.warning(
            "CRITICAL RESULT ALERT: Doctor '%s' notified for test '%s'. Result: %s %s",
            req.requested_by or req.created_by, req.test_name, body.result_value, body.unit or ""
        )

    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result


async def update_lab_result(
    db: AsyncSession,
    request_id: UUID,
    body: ResultUpdateRequest,
    user: TokenPayload,
) -> dict:
    stmt = select(LabResult).where(LabResult.request_id == request_id)
    result = (await db.execute(stmt)).scalar_one_or_none()
    if not result:
        raise NotFoundError("No lab result found for this request")

    if result.status == "verified":
        raise UnprocessableEntityError("Cannot modify a verified lab result")

    if body.result_value is not None:
        result.result_value = body.result_value
    if body.unit is not None:
        result.unit = body.unit
    if body.reference_range is not None:
        result.reference_range = body.reference_range
    if body.result_notes is not None:
        result.result_notes = body.result_notes

    if body.is_critical is not None:
        if body.is_critical and not result.is_critical:
            result.is_critical = True
            if not result.critical_notified_at:
                result.critical_notified_at = datetime.now(timezone.utc)
                logger.warning("CRITICAL RESULT ALERT (AMENDED): Result ID %s marked critical", result.result_id)
        elif not body.is_critical:
            result.is_critical = False

    result.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(result)

    return {
        "result_id": result.result_id,
        "result_value": result.result_value,
        "is_critical": result.is_critical,
        "status": result.status,
    }


async def get_lab_result_by_request(db: AsyncSession, request_id: UUID) -> dict:
    stmt = select(LabResult).where(LabResult.request_id == request_id)
    result = (await db.execute(stmt)).scalar_one_or_none()
    if not result:
        raise NotFoundError("No lab result found for this request")

    performed_by_name = await _resolve_user_name(db, result.performed_by)
    verified_by_name = await _resolve_user_name(db, result.verified_by)

    return {
        "result_id": result.result_id,
        "request_id": result.request_id,
        "specimen_type": result.specimen_type,
        "specimen_label": result.specimen_label,
        "result_value": result.result_value,
        "unit": result.unit,
        "reference_range": result.reference_range,
        "is_critical": result.is_critical,
        "critical_notified_at": result.critical_notified_at,
        "result_notes": result.result_notes,
        "performed_by_name": performed_by_name,
        "verified_by_name": verified_by_name,
        "status": result.status,
        "resulted_at": result.resulted_at,
    }


# ── Group 4: Result Verification ─────────────────────────────────────────────

async def verify_lab_result(db: AsyncSession, result_id: UUID, user: TokenPayload) -> dict:
    stmt = select(LabResult).where(LabResult.result_id == result_id)
    result = (await db.execute(stmt)).scalar_one_or_none()
    if not result:
        raise NotFoundError("Lab result record not found")

    if result.status != "resulted":
        raise UnprocessableEntityError(f"Cannot verify result in status '{result.status}'. Must be 'resulted'.")

    user_id_str = _user_identifier(user)
    now_dt = datetime.now(timezone.utc)

    result.status = "verified"
    result.verified_by = user_id_str
    result.verified_at = now_dt
    result.updated_at = now_dt

    # Update parent investigation request
    req_stmt = select(InvestigationRequest).where(InvestigationRequest.id == result.request_id)
    req = (await db.execute(req_stmt)).scalar_one_or_none()
    if req:
        req.status = "completed"

    # Update active specimen
    spec_stmt = select(Specimen).where(
        and_(
            Specimen.request_id == result.request_id,
            Specimen.status != "rejected"
        )
    )
    spec = (await db.execute(spec_stmt)).scalar_one_or_none()
    if spec:
        spec.status = "completed"
        spec.updated_at = now_dt

    await db.commit()
    await db.refresh(result)

    return {
        "result_id": result.result_id,
        "status": "verified",
        "verified_by": user_id_str,
        "request_status": "completed",
    }


# ── Group 5: Billing ─────────────────────────────────────────────────────────

async def create_lab_bill(
    db: AsyncSession,
    request_id: UUID,
    body: LabBillCreateRequest,
    user: TokenPayload,
) -> dict:
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    req = (await db.execute(stmt)).scalar_one_or_none()
    if not req:
        raise NotFoundError("Investigation request not found")

    if req.status != "completed":
        raise UnprocessableEntityError(f"Cannot bill for request in status '{req.status}'. Must be 'completed'.")

    # Check for existing bill item for this request
    check_item = select(BillItem).where(
        and_(
            BillItem.reference_id == request_id,
            BillItem.item_type == "lab"
        )
    )
    existing_item = (await db.execute(check_item)).scalar_one_or_none()
    if existing_item:
        raise ConflictError("Bill item already exists for this investigation request")

    # Lookup open bill for visit
    bill_stmt = select(Bill).where(
        and_(
            Bill.visit_id == req.visit_id,
            Bill.status == "open"
        )
    )
    bill = (await db.execute(bill_stmt)).scalar_one_or_none()
    if not bill:
        raise NotFoundError("No open bill found for this visit")

    bill_item = BillItem(
        bill_id=bill.bill_id,
        item_type="lab",
        description=body.description,
        quantity=1,
        unit_price=body.unit_price,
        total_price=body.unit_price,
        reference_id=request_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(bill_item)

    bill.total_amount += body.unit_price

    await db.commit()
    await db.refresh(bill_item)

    return {
        "item_id": bill_item.bill_item_id,
        "bill_id": bill.bill_id,
        "item_type": bill_item.item_type,
        "description": bill_item.description,
        "unit_price": bill_item.unit_price,
        "total_price": bill_item.total_price,
        "reference_id": bill_item.reference_id,
    }


# ── Group 6: Doctor-Facing Result Read ───────────────────────────────────────

async def get_visit_verified_results(db: AsyncSession, visit_id: UUID) -> dict:
    stmt = (
        select(InvestigationRequest, LabResult)
        .join(LabResult, LabResult.request_id == InvestigationRequest.id)
        .where(
            and_(
                InvestigationRequest.visit_id == visit_id,
                InvestigationRequest.request_type.in_(["lab", "laboratory"]),
                LabResult.status == "verified"
            )
        )
        .order_by(LabResult.resulted_at.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    results_list = []
    for req, lr in rows:
        performed_by_name = await _resolve_user_name(db, lr.performed_by)
        results_list.append({
            "request_id": req.id,
            "test_name": req.test_name,
            "test_code": req.test_code,
            "urgency": req.urgency or "routine",
            "result_id": lr.result_id,
            "result_value": lr.result_value,
            "unit": lr.unit,
            "reference_range": lr.reference_range,
            "is_critical": lr.is_critical,
            "result_notes": lr.result_notes,
            "performed_by_name": performed_by_name,
            "resulted_at": lr.resulted_at,
        })

    return {
        "visit_id": visit_id,
        "results": results_list,
    }
