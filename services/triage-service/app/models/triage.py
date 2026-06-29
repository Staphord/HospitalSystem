import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class TriageAssessment(Base):
    __tablename__ = "triage_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    patient_id = Column(String(50), nullable=False, index=True)
    
    # Vital signs
    blood_pressure = Column(String(20), nullable=True)
    temperature = Column(Float, nullable=True)
    pulse = Column(Integer, nullable=True)
    oxygen_saturation = Column(Float, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)
    
    # Presenting complaint
    presenting_complaint = Column(Text, nullable=True)
    structured_complaint = Column(String(255), nullable=True)
    
    # Triage Classification
    triage_category = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)
