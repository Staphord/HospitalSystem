from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, BadRequestError
from app.models.radiology import RadiologyReport


VALID_MODALITIES = {"xray", "ct", "mri", "ultrasound", "fluoroscopy", "mammography", "other"}
VALID_STATUSES = {"scheduled", "performed", "reported", "verified"}


async def create_report(db: AsyncSession, data: dict) -> RadiologyReport:
    modality = data.get("modality", "")
    if modality not in VALID_MODALITIES:
        raise BadRequestError(f"Invalid modality '{modality}'. Must be one of: {', '.join(sorted(VALID_MODALITIES))}")

    status = data.get("status", "scheduled")
    if status not in VALID_STATUSES:
        raise BadRequestError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}")

    reported_at = None
    if status == "reported":
        reported_at = datetime.now(timezone.utc)

    report = RadiologyReport(
        request_id=data.get("request_id"),
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
        if data["modality"] not in VALID_MODALITIES:
            raise BadRequestError(f"Invalid modality '{data['modality']}'")
        report.modality = data["modality"]
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
    if "performed_by" in data:
        report.performed_by = data["performed_by"]
    if "reported_by" in data:
        report.reported_by = data["reported_by"]
    if "status" in data and data["status"] is not None:
        if data["status"] not in VALID_STATUSES:
            raise BadRequestError(f"Invalid status '{data['status']}'")
        report.status = data["status"]
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