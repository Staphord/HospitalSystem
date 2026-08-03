import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Patient(Base):
    """Read model for radiology worklist joins (shared tenant table)."""

    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=True)
    patient_number = Column(String(20), nullable=True)
    full_name = Column(String(200), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=False)
    phone_primary = Column(String(20), nullable=True)
    email = Column(String(150), nullable=True)
    address = Column(Text, nullable=True)
    allergies = Column(Text, nullable=True)


class Visit(Base):
    """Read model for radiology worklist joins (shared tenant table)."""

    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    visit_number = Column(String(20), nullable=False, unique=True)
    visit_date = Column(Date, nullable=True)
    visit_type = Column(String(50), nullable=True)
    payment_type = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default="registered")


class InvestigationRequest(Base):
    """Shared investigation requests — radiology filters request_type='radiology'."""

    __tablename__ = "investigation_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), nullable=False)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    request_type = Column(String(50), nullable=False)
    test_name = Column(String(255), nullable=False)
    clinical_history = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    urgency = Column(String(50), nullable=False, default="routine")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(255), nullable=True)


class RadiologyReport(Base):
    __tablename__ = "radiology_reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("investigation_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    visit_id = Column(UUID(as_uuid=True), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    modality = Column(
        Enum(
            "xray",
            "ct",
            "mri",
            "ultrasound",
            "fluoroscopy",
            "mammography",
            "other",
            name="modality_enum",
            create_constraint=False,
        ),
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
        Enum(
            "scheduled",
            "performed",
            "reported",
            "verified",
            name="report_status_enum",
            create_constraint=False,
        ),
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
