from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime, date

class TriageAssessmentCreate(BaseModel):
    visit_id: UUID
    patient_id: UUID
    
    # Vitals (individually optional)
    blood_pressure_systolic: Optional[int] = Field(None, gt=0, description="Systolic BP in mmHg")
    blood_pressure_diastolic: Optional[int] = Field(None, gt=0, description="Diastolic BP in mmHg")
    temperature: Optional[float] = Field(None, ge=25.0, le=45.0, description="Temperature in °C")
    pulse_rate: Optional[int] = Field(None, gt=0, description="Pulse rate in bpm")
    oxygen_saturation: Optional[float] = Field(None, ge=0.0, le=100.0, description="SpO2 percentage")
    respiratory_rate: Optional[int] = Field(None, gt=0, description="Breaths per minute")
    weight_kg: Optional[float] = Field(None, gt=0.0, description="Weight in kg")
    
    # Complaints
    chief_complaint: str = Field(..., min_length=1, description="Mandatory free-text presenting complaint")
    complaint_code: Optional[str] = Field(None, max_length=20, description="Optional structured complaint code")
    
    # Category (mandatory)
    triage_category: str = Field(..., description="emergency, urgent, semi_urgent, non_urgent")
    triage_notes: Optional[str] = None


class VitalsResponse(BaseModel):
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    temperature: Optional[float] = None
    pulse_rate: Optional[int] = None
    oxygen_saturation: Optional[float] = None
    respiratory_rate: Optional[int] = None
    weight_kg: Optional[float] = None


class DoctorQueueEntryResponse(BaseModel):
    queue_id: UUID
    queue_type: str
    priority: str
    status: str


class TriageAssessmentResponse(BaseModel):
    triage_id: UUID
    visit_id: UUID
    patient_id: UUID
    triage_nurse_id: UUID
    vitals: VitalsResponse
    chief_complaint: str
    complaint_code: Optional[str] = None
    triage_category: str
    triage_notes: Optional[str] = None
    assessed_at: datetime
    doctor_queue_entry: Optional[DoctorQueueEntryResponse] = None

    class Config:
        from_attributes = True


class PatientSummary(BaseModel):
    patient_id: UUID
    full_name: str
    date_of_birth: date
    gender: str


class NurseSummary(BaseModel):
    user_id: UUID
    full_name: str


class TriageSummaryResponse(BaseModel):
    triage_id: UUID
    visit_id: UUID
    patient: PatientSummary
    triage_nurse: NurseSummary
    vitals: VitalsResponse
    chief_complaint: str
    complaint_code: Optional[str] = None
    triage_category: str
    triage_notes: Optional[str] = None
    assessed_at: datetime

    class Config:
        from_attributes = True


class VitalsInput(BaseModel):
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    temperature: Optional[float] = None
    pulse_rate: Optional[int] = None
    oxygen_saturation: Optional[float] = None
    respiratory_rate: Optional[int] = None
    weight_kg: Optional[float] = None


class TriageCategorySuggestionResponse(BaseModel):
    suggested_category: str
    reason: str


class PatientQueueInfo(BaseModel):
    patient_id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str


class VisitQueueInfo(BaseModel):
    visit_id: UUID
    visit_number: str
    visit_type: str
    payment_type: str


class TriageQueueItem(BaseModel):
    queue_id: UUID
    queue_number: str
    priority: str
    status: str
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    patient: PatientQueueInfo
    visit: VisitQueueInfo
    created_at: datetime


class TriageQueueResponse(BaseModel):
    queue: list[TriageQueueItem]
    total: int


class QueueCallResponse(BaseModel):
    queue_id: UUID
    status: str
    called_at: Optional[datetime] = None


class QueueSkipResponse(BaseModel):
    queue_id: UUID
    status: str
    completed_at: Optional[datetime] = None
