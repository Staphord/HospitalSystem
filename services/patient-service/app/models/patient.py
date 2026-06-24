import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class TenantPatient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=False, index=True)

    patient_number = Column(String(30), nullable=False)
    full_name = Column(String(200), nullable=False, index=True)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=False)
    phone = Column(String(30))
    email = Column(String(150))
    address = Column(Text)
    emergency_contact_name = Column(String(200))
    emergency_contact_phone = Column(String(30))
    national_id = Column(String(50), nullable=True)
    medical_history = Column(Text)
    allergies = Column(Text)
    blood_group = Column(String(10))
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint("hospital_id", "patient_number", name="uq_hospital_patient_number"),
        UniqueConstraint("hospital_id", "national_id", name="uq_hospital_national_id"),
    )


class PatientNumberSequence(Base):
    __tablename__ = "patient_number_sequences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hospital_id = Column(String(50), nullable=False, index=True)
    date_key = Column(String(8), nullable=False)
    counter = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("hospital_id", "date_key", name="uq_hospital_date_key"),
    )
