import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class RadiologyReport(Base):
    __tablename__ = "radiology_reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), nullable=True)
    visit_id = Column(UUID(as_uuid=True), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    modality = Column(
        Enum("xray", "ct", "mri", "ultrasound", "fluoroscopy", "mammography", "other", name="modality_enum"),
        nullable=False,
    )
    body_part = Column(String(100))
    scheduled_at = Column(DateTime(timezone=True))
    performed_at = Column(DateTime(timezone=True))
    findings = Column(Text)
    impression = Column(Text)
    image_reference = Column(String(255))
    performed_by = Column(UUID(as_uuid=True), nullable=False)
    reported_by = Column(UUID(as_uuid=True))
    status = Column(
        Enum("scheduled", "performed", "reported", "verified", name="report_status_enum"),
        nullable=False,
        default="scheduled",
    )
    reported_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
