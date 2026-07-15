import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class TriageAssessment(Base):
    __tablename__ = "triage_assessments"

    triage_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    triage_nurse_id = Column(UUID(as_uuid=True), nullable=False)
    
    # Vital signs
    blood_pressure_systolic = Column(Integer, nullable=True)
    blood_pressure_diastolic = Column(Integer, nullable=True)
    temperature = Column(Float, nullable=True)
    pulse_rate = Column(Integer, nullable=True)
    oxygen_saturation = Column(Float, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    
    # Presenting complaint
    chief_complaint = Column(Text, nullable=False)
    complaint_code = Column(String(20), nullable=True)
    
    # Triage Classification
    triage_category = Column(String(50), nullable=False)
    triage_notes = Column(Text, nullable=True)
    
    # Metadata
    assessed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=False)
    patient_number = Column(String(30), nullable=False)
    full_name = Column(String(200), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=False)


class Visit(Base):
    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(String(36), nullable=False)
    visit_number = Column(String(20), nullable=False, unique=True)
    visit_type = Column(String(50), nullable=False)
    payment_type = Column(String(50), nullable=False)
    queue_number = Column(String(10), nullable=True)
    status = Column(String(50), nullable=False, default="registered")
    visit_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class Queue(Base):
    __tablename__ = "queues"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(String(36), nullable=False)
    queue_type = Column(String(50), nullable=False)
    queue_number = Column(String(10), nullable=False)
    priority = Column(String(50), nullable=False, default="non_urgent")
    status = Column(String(50), nullable=False, default="waiting")
    called_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
