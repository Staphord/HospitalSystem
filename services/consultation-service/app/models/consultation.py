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
    triage_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False, unique=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    triage_nurse_id = Column(UUID(as_uuid=True), nullable=True)
    blood_pressure_systolic = Column(Integer, nullable=True)
    blood_pressure_diastolic = Column(Integer, nullable=True)
    temperature = Column(Float, nullable=True)
    pulse_rate = Column(Integer, nullable=True)
    oxygen_saturation = Column(Float, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    chief_complaint = Column(Text, nullable=False)
    complaint_code = Column(String(20), nullable=True)
    triage_category = Column(String(50), nullable=False)
    triage_notes = Column(Text, nullable=True)
    assessed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Consultation(Base):
    __tablename__ = "consultations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False, unique=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    history_of_presenting_illness = Column(Text, nullable=True)
    examination_findings = Column(Text, nullable=True)
    clinical_impression = Column(Text, nullable=True)
    consultation_status = Column(String(50), nullable=False, default="in_progress")
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    disposition = Column(String(50), nullable=True)
    referral_type = Column(String(50), nullable=True)
    referral_notes = Column(Text, nullable=True)
    admission_reason = Column(Text, nullable=True)
    discharge_instructions = Column(Text, nullable=True)
    follow_up_date = Column(Date, nullable=True)
    return_date = Column(Date, nullable=True)
    return_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)

    diagnoses = relationship("Diagnosis", back_populates="consultation", cascade="all, delete-orphan", lazy="selectin")
    investigation_requests = relationship("InvestigationRequest", back_populates="consultation", cascade="all, delete-orphan", lazy="selectin")
    prescriptions = relationship("Prescription", back_populates="consultation", cascade="all, delete-orphan", lazy="selectin")

class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    diagnosis_type = Column(String(50), nullable=False)
    code = Column(String(20), nullable=True)
    description = Column(Text, nullable=False)
    sequence_order = Column(Integer, nullable=True)
    recorded_by = Column(String(255), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    consultation = relationship("Consultation", back_populates="diagnoses")

class InvestigationRequest(Base):
    __tablename__ = "investigation_requests"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    request_type = Column(String(50), nullable=False)
    test_name = Column(String(255), nullable=False)
    test_code = Column(String(50), nullable=True)
    clinical_history = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    urgency = Column(String(50), nullable=False, default="routine")
    requested_by = Column(String(255), nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)

    consultation = relationship("Consultation", back_populates="investigation_requests")

class Prescription(Base):
    __tablename__ = "prescriptions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    drug_name = Column(String(200), nullable=False)
    dose = Column(String(50), nullable=False)
    frequency = Column(String(50), nullable=False)
    duration = Column(String(50), nullable=False)
    route = Column(String(50), nullable=False)
    instructions = Column(Text, nullable=True)
    prescribed_by = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    prescribed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    consultation = relationship("Consultation", back_populates="prescriptions")

class Queue(Base):
    __tablename__ = "queues"
    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    queue_type = Column(String(50), nullable=False)
    queue_number = Column(String(10), nullable=False)
    priority = Column(String(50), nullable=False, default="non_urgent")
    status = Column(String(50), nullable=False, default="waiting")
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class FeeSchedule(Base):
    __tablename__ = "fee_schedules"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_code = Column(String(50), nullable=True)
    item_name = Column(String(200), nullable=False)
    item_type = Column(String(50), nullable=False)
    standard_price = Column(Float, nullable=False, default=0.0)
    insurance_price = Column(Float, nullable=False, default=0.0)
    is_active = Column(Boolean, default=True, nullable=False)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)

class Bill(Base):
    __tablename__ = "bills"
    bill_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    total_amount = Column(Float, nullable=False, default=0.0)
    status = Column(String(50), nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class BillItem(Base):
    __tablename__ = "bill_items"
    bill_item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bill_id = Column(UUID(as_uuid=True), ForeignKey("bills.bill_id", ondelete="CASCADE"), nullable=False)
    item_type = Column(String(50), nullable=False)
    description = Column(String(200), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0.0)
    total_price = Column(Float, nullable=False, default=0.0)
    reference_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class LabResult(Base):
    __tablename__ = "lab_results"
    result_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("investigation_requests.id", ondelete="CASCADE"), nullable=False)
    visit_id = Column(UUID(as_uuid=True), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    specimen_type = Column(String(100), nullable=False)
    result_value = Column(Text, nullable=False)
    unit = Column(String(50), nullable=True)
    reference_range = Column(String(100), nullable=True)
    is_critical = Column(Boolean, default=False, nullable=False)
    result_notes = Column(Text, nullable=True)
    performed_by = Column(String(255), nullable=False)
    verified_by = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="resulted")
    resulted_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)

class RadiologyReport(Base):
    __tablename__ = "radiology_reports"
    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("investigation_requests.id", ondelete="CASCADE"), nullable=True)
    visit_id = Column(UUID(as_uuid=True), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    modality = Column(String(50), nullable=False)
    body_part = Column(String(100), nullable=True)
    findings = Column(Text, nullable=True)
    impression = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="scheduled")
    reported_by = Column(UUID(as_uuid=True), nullable=True)
    reported_at = Column(DateTime, nullable=True)
