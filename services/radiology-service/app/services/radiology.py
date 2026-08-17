from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import publish_radiology_report_ready
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.radiology import InvestigationRequest, Patient, RadiologyReport, Visit

if TYPE_CHECKING:
    from app.core.security import TokenPayload
else:
    TokenPayload = Any

VALID_MODALITIES = {"xray", "ct", "mri", "ultrasound", "fluoroscopy", "mammography", "other"}
VALID_REPORT_STATUSES = {"scheduled", "performed", "reported", "verified"}
RADIOLOGY_REQUEST_TYPE = "radiology"
VALID_REQUEST_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


def _parse_user_uuid(user: TokenPayload | None, explicit: UUID | None = None) -> UUID:
    if explicit is not None:
        return explicit
    if user and user.sub:
        try:
            return UUID(str(user.sub))
        except (ValueError, TypeError):
            pass
    raise BadRequestError("performed_by / reported_by UUID is required")


def _validate_modality(modality: str) -> str:
    if modality not in VALID_MODALITIES:
        raise BadRequestError(
            f"Invalid modality '{modality}'. Must be one of: {', '.join(sorted(VALID_MODALITIES))}"
        )
    return modality


def _validate_report_status(status: str) -> str:
    if status not in VALID_REPORT_STATUSES:
        raise BadRequestError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_REPORT_STATUSES))}"
        )
    return status


async def _get_radiology_request(db: AsyncSession, request_id: UUID) -> InvestigationRequest:
    result = await db.execute(
        select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise NotFoundError(f"Investigation request {request_id} not found")
    if req.request_type != RADIOLOGY_REQUEST_TYPE:
        raise ConflictError("Requested investigation is not a radiology study")
    return req


async def _get_report_by_request(db: AsyncSession, request_id: UUID) -> RadiologyReport | None:
    result = await db.execute(
        select(RadiologyReport).where(RadiologyReport.request_id == request_id)
    )
    return result.scalar_one_or_none()


async def _set_request_status(db: AsyncSession, req: InvestigationRequest, status: str) -> None:
    if status not in VALID_REQUEST_STATUSES and status != "specimen_collected":
        raise BadRequestError(f"Invalid request status '{status}'")
    req.status = status


# ── Report CRUD ─────────────────────────────────────────────────────────────────

async def create_report(db: AsyncSession, data: dict) -> RadiologyReport:
    modality = _validate_modality(data.get("modality", ""))
    status = _validate_report_status(data.get("status", "scheduled"))

    request_id = data.get("request_id")
    if not request_id:
        raise BadRequestError("request_id is required")

    await _get_radiology_request(db, request_id)

    existing = await _get_report_by_request(db, request_id)
    if existing:
        raise ConflictError(f"Report already exists for request {request_id}")

    reported_at = datetime.now(timezone.utc) if status == "reported" else None

    report = RadiologyReport(
        request_id=request_id,
        visit_id=data["visit_id"],
        patient_id=data["patient_id"],
        modality=modality,
        body_part=data.get("body_part"),
        scheduled_at=data.get("scheduled_at"),
        performed_at=data.get("performed_at"),
        findings=data.get("findings"),
        impression=data.get("impression"),
        image_reference=data.get("image_reference"),
        performed_by=data["performed_by"],
        reported_by=data.get("reported_by"),
        status=status,
        reported_at=reported_at,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def get_report(db: AsyncSession, report_id: UUID) -> RadiologyReport:
    result = await db.execute(select(RadiologyReport).where(RadiologyReport.report_id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise NotFoundError(f"Radiology report {report_id} not found")
    return report


async def list_reports(
    db: AsyncSession,
    patient_id: UUID | None = None,
    visit_id: UUID | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[RadiologyReport], int]:
    query = select(RadiologyReport)
    count_query = select(func.count(RadiologyReport.report_id))

    if patient_id:
        query = query.where(RadiologyReport.patient_id == patient_id)
        count_query = count_query.where(RadiologyReport.patient_id == patient_id)
    if visit_id:
        query = query.where(RadiologyReport.visit_id == visit_id)
        count_query = count_query.where(RadiologyReport.visit_id == visit_id)
    if status:
        query = query.where(RadiologyReport.status == status)
        count_query = count_query.where(RadiologyReport.status == status)

    query = query.order_by(RadiologyReport.created_at.desc()).offset(skip).limit(limit)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query)
    reports = list(result.scalars().all())
    return reports, total


async def update_report(db: AsyncSession, report_id: UUID, data: dict) -> RadiologyReport:
    report = await get_report(db, report_id)

    if "modality" in data and data["modality"] is not None:
        report.modality = _validate_modality(data["modality"])
    if "body_part" in data:
        report.body_part = data["body_part"]
    if "scheduled_at" in data:
        report.scheduled_at = data["scheduled_at"]
    if "performed_at" in data:
        report.performed_at = data["performed_at"]
    if "findings" in data:
        report.findings = data["findings"]
    if "impression" in data:
        report.impression = data["impression"]
    if "image_reference" in data:
        report.image_reference = data["image_reference"]
    if "performed_by" in data and data["performed_by"] is not None:
        report.performed_by = data["performed_by"]
    if "reported_by" in data:
        report.reported_by = data["reported_by"]
    if "status" in data and data["status"] is not None:
        report.status = _validate_report_status(data["status"])
        if data["status"] == "reported" and not report.reported_at:
            report.reported_at = datetime.now(timezone.utc)

    report.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(report)
    return report


async def delete_report(db: AsyncSession, report_id: UUID) -> None:
    report = await get_report(db, report_id)
    await db.delete(report)
    await db.commit()


# ── Imaging request worklist ────────────────────────────────────────────────────

async def list_imaging_requests(
    db: AsyncSession,
    status: str | None = None,
    urgency: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    urgency_order = case(
        (InvestigationRequest.urgency == "stat", 1),
        (InvestigationRequest.urgency == "urgent", 2),
        (InvestigationRequest.urgency == "routine", 3),
        else_=4,
    )

    base = (
        select(InvestigationRequest, Patient, Visit, RadiologyReport)
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .join(Visit, InvestigationRequest.visit_id == Visit.visit_id)
        .outerjoin(RadiologyReport, RadiologyReport.request_id == InvestigationRequest.id)
        .where(InvestigationRequest.request_type == RADIOLOGY_REQUEST_TYPE)
    )

    count_base = (
        select(func.count(InvestigationRequest.id))
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .where(InvestigationRequest.request_type == RADIOLOGY_REQUEST_TYPE)
    )

    if status:
        base = base.where(InvestigationRequest.status == status)
        count_base = count_base.where(InvestigationRequest.status == status)
    if urgency:
        base = base.where(InvestigationRequest.urgency == urgency)
        count_base = count_base.where(InvestigationRequest.urgency == urgency)
    if search:
        pattern = f"%{search}%"
        search_filter = or_(
            Patient.full_name.ilike(pattern),
            Patient.patient_number.ilike(pattern),
            InvestigationRequest.test_name.ilike(pattern),
        )
        base = base.where(search_filter)
        count_base = count_base.where(search_filter)

    total = (await db.execute(count_base)).scalar() or 0

    rows = (
        await db.execute(
            base.order_by(urgency_order, InvestigationRequest.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
    ).all()

    items: list[dict] = []
    for req, pat, vis, report in rows:
        items.append(
            {
                "request_id": req.id,
                "test_name": req.test_name,
                "clinical_indication": req.clinical_history,
                "urgency": req.urgency or "routine",
                "status": req.status,
                "requested_by": req.created_by,
                "requested_at": req.created_at,
                "patient_name": pat.full_name,
                "patient_number": pat.patient_number or "P-000",
                "visit_id": vis.visit_id,
                "visit_number": vis.visit_number,
                "report_id": report.report_id if report else None,
                "report_status": report.status if report else None,
                "modality": report.modality if report else None,
                "scheduled_at": report.scheduled_at if report else None,
            }
        )
    return items, total


async def get_imaging_request_detail(db: AsyncSession, request_id: UUID) -> dict:
    stmt = (
        select(InvestigationRequest, Patient, Visit, RadiologyReport)
        .join(Patient, InvestigationRequest.patient_id == Patient.id)
        .join(Visit, InvestigationRequest.visit_id == Visit.visit_id)
        .outerjoin(RadiologyReport, RadiologyReport.request_id == InvestigationRequest.id)
        .where(InvestigationRequest.id == request_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if not row:
        raise NotFoundError(f"Investigation request {request_id} not found")

    req, pat, vis, report = row
    if req.request_type != RADIOLOGY_REQUEST_TYPE:
        raise ConflictError("Requested investigation is not a radiology study")

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
            "allergies": pat.allergies,
        },
        "visit": {
            "visit_id": vis.visit_id,
            "visit_number": vis.visit_number,
            "visit_date": vis.visit_date or date.today(),
            "visit_type": vis.visit_type or "outpatient",
            "payment_type": vis.payment_type or "cash",
        },
        "report": report,
    }


# ── Workflow actions ────────────────────────────────────────────────────────────

async def schedule_imaging(
    db: AsyncSession,
    request_id: UUID,
    data: dict,
    user: TokenPayload,
    tenant_id: str | None = None,
) -> RadiologyReport:
    req = await _get_radiology_request(db, request_id)
    if req.status == "cancelled":
        raise ConflictError("Cannot schedule a cancelled request")
    if req.status == "completed":
        raise ConflictError("Cannot schedule a completed request")

    modality = _validate_modality(data["modality"])
    performed_by = _parse_user_uuid(user, data.get("performed_by"))
    scheduled_at = data["scheduled_at"]

    report = await _get_report_by_request(db, request_id)
    if report:
        if report.status in {"reported", "verified"}:
            raise ConflictError(f"Cannot reschedule a report in status '{report.status}'")
        report.modality = modality
        report.body_part = data.get("body_part", report.body_part)
        report.scheduled_at = scheduled_at
        report.performed_by = performed_by
        report.status = "scheduled"
        report.updated_at = datetime.now(timezone.utc)
    else:
        report = RadiologyReport(
            request_id=req.id,
            visit_id=req.visit_id,
            patient_id=req.patient_id,
            modality=modality,
            body_part=data.get("body_part"),
            scheduled_at=scheduled_at,
            performed_by=performed_by,
            status="scheduled",
        )
        db.add(report)

    # Request stays "pending" until perform_imaging transitions it — scheduling
    # only creates/updates the report row above.

    await db.commit()
    await db.refresh(report)
    return report


async def perform_imaging(
    db: AsyncSession,
    request_id: UUID,
    data: dict,
    user: TokenPayload,
) -> RadiologyReport:
    req = await _get_radiology_request(db, request_id)
    report = await _get_report_by_request(db, request_id)
    if not report:
        raise ConflictError("Schedule the imaging study before marking it performed")
    if report.status in {"reported", "verified"}:
        raise ConflictError(f"Cannot perform a report in status '{report.status}'")

    report.performed_at = data.get("performed_at") or datetime.now(timezone.utc)
    if data.get("performed_by") is not None or user.sub:
        try:
            report.performed_by = _parse_user_uuid(user, data.get("performed_by"))
        except BadRequestError:
            pass
    report.status = "performed"
    report.updated_at = datetime.now(timezone.utc)

    await _set_request_status(db, req, "in_progress")

    await db.commit()
    await db.refresh(report)
    return report


async def enter_report(
    db: AsyncSession,
    request_id: UUID,
    data: dict,
    user: TokenPayload,
    tenant_id: str | None = None,
) -> RadiologyReport:
    req = await _get_radiology_request(db, request_id)
    report = await _get_report_by_request(db, request_id)
    if not report:
        raise ConflictError("Schedule/perform the imaging study before entering a report")
    if report.status == "verified":
        raise ConflictError("Cannot edit a verified report")

    report.findings = data["findings"]
    report.impression = data["impression"]
    if "image_reference" in data:
        report.image_reference = data.get("image_reference")
    try:
        report.reported_by = _parse_user_uuid(user, data.get("reported_by"))
    except BadRequestError:
        report.reported_by = None
    report.status = "reported"
    report.reported_at = datetime.now(timezone.utc)
    report.updated_at = datetime.now(timezone.utc)

    await _set_request_status(db, req, "completed")

    await db.commit()
    await db.refresh(report)

    if tenant_id:
        await publish_radiology_report_ready(
            report_id=str(report.report_id),
            request_id=str(report.request_id),
            tenant_id=tenant_id,
            status=report.status,
        )
    return report


async def verify_report(
    db: AsyncSession,
    report_id: UUID,
    user: TokenPayload,
    tenant_id: str | None = None,
) -> RadiologyReport:
    report = await get_report(db, report_id)
    if report.status not in {"reported", "verified"}:
        raise ConflictError("Only a reported study can be verified")

    if report.status != "verified":
        report.status = "verified"
        report.updated_at = datetime.now(timezone.utc)
        try:
            report.reported_by = report.reported_by or _parse_user_uuid(user, None)
        except BadRequestError:
            pass

        req = await _get_radiology_request(db, report.request_id)
        await _set_request_status(db, req, "completed")

        await db.commit()
        await db.refresh(report)

        if tenant_id:
            await publish_radiology_report_ready(
                report_id=str(report.report_id),
                request_id=str(report.request_id),
                tenant_id=tenant_id,
                status=report.status,
            )
    return report
