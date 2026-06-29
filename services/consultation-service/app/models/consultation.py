import uuid
from datetime import date, datetime
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, ForeignKey, Date, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base

class Patient(Base):
    __tablename__ = "patients"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=False)
    patient_number = Column(String(30), nullable=False)
    full_name = Column(String(200), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=False)
    phone_primary = Column(String(20), nullable=False)
    phone_secondary = Column(String(20))
    email = Column(String(150))
    address = Column(Text)
    next_of_kin_name = Column(String(200))
    next_of_kin_phone = Column(String(20))
    next_of_kin_relationship = Column(String(50))
    national_id = Column(String(50))
    allergies = Column(Text)
    blood_group = Column(String(10))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Visit(Base):
    __tablename__ = "visits"
    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    visit_number = Column(String(20), nullable=False)
    visit_date = Column(Date, nullable=False, default=date.today)
    visit_type = Column(String(50), nullable=False)
    payment_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="registered")
    created_at = Column(DateTime, default=datetime.utcnow)

class TriageAssessment(Base):
    __tablename__ = "triage_assessments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False, unique=True)
    patient_id = Column(String(50), nullable=False)
    blood_pressure = Column(String(20), nullable=True)
    temperature = Column(Float, nullable=True)
    pulse = Column(Integer, nullable=True)
    oxygen_saturation = Column(Float, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)
    presenting_complaint = Column(Text, nullable=True)
    triage_category = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Consultation(Base):
    __tablename__ = "consultations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False, unique=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    history_of_presenting_illness = Column(Text, nullable=True)
    examination_findings = Column(Text, nullable=True)
    clinical_impression = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)

    diagnoses = relationship("Diagnosis", back_populates="consultation", cascade="all, delete-orphan", lazy="selectin")
    investigation_requests = relationship("InvestigationRequest", back_populates="consultation", cascade="all, delete-orphan", lazy="selectin")

class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    diagnosis_type = Column(String(50), nullable=False)  # "provisional" or "differential"
    code = Column(String(20), nullable=True)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    consultation = relationship("Consultation", back_populates="diagnoses")

class InvestigationRequest(Base):
    __tablename__ = "investigation_requests"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    request_type = Column(String(50), nullable=False)  # "laboratory" or "radiology"
    test_name = Column(String(255), nullable=False)
    clinical_history = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="pending")  # "pending", "completed", "cancelled"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)

    consultation = relationship("Consultation", back_populates="investigation_requests")
