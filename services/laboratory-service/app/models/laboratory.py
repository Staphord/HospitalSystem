import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Date
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class Patient(Base):
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
    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    visit_number = Column(String(20), nullable=False, unique=True)
    visit_date = Column(Date, nullable=True)
    visit_type = Column(String(50), nullable=True)
    payment_type = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default="registered")


class InvestigationRequest(Base):
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
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(255), nullable=True)


class Queue(Base):
    __tablename__ = "queues"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    queue_type = Column(String(50), nullable=False)
    queue_number = Column(String(10), nullable=False)
    priority = Column(String(50), nullable=False, default="non_urgent")
    status = Column(String(50), nullable=False, default="waiting")
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Specimen(Base):
    __tablename__ = "specimens"

    specimen_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("investigation_requests.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    specimen_type = Column(String(100), nullable=False)
    collection_site = Column(String(100), nullable=True)
    collected_by = Column(String(255), nullable=False)
    collected_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False, default="collected")
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class LabResult(Base):
    __tablename__ = "lab_results"

    result_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("investigation_requests.id", ondelete="CASCADE"), nullable=False)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    specimen_type = Column(String(100), nullable=False)
    result_value = Column(Text, nullable=False)
    unit = Column(String(50), nullable=True)
    reference_range = Column(String(100), nullable=True)
    is_critical = Column(Boolean, nullable=False, default=False)
    result_notes = Column(Text, nullable=True)
    performed_by = Column(String(255), nullable=False)
    verified_by = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="resulted")
    resulted_at = Column(DateTime(timezone=True), nullable=False)
    critical_notified_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
