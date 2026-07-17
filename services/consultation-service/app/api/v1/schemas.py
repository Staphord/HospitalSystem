import uuid
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, Field

# Diagnosis
class DiagnosisCreate(BaseModel):
    diagnosis_type: str = Field(..., description="'provisional', 'differential', or 'final'")
    code: Optional[str] = Field(None, description="Optional ICD-10 code")
    description: str = Field(..., description="Diagnosis text or description")


class DispositionRequest(BaseModel):
    disposition: str = Field(..., description="outpatient | admission | referral | deceased")
    notes: Optional[str] = None


class DispositionResponse(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    patient_id: uuid.UUID
    disposition: Optional[str]
    disposition_notes: Optional[str]
    updated_at: datetime

    class Config:
        from_attributes = True

class DiagnosisResponse(BaseModel):
    id: uuid.UUID
    consultation_id: uuid.UUID
    diagnosis_type: str
    code: Optional[str]
    description: str
    created_at: datetime

    class Config:
        from_attributes = True

# Investigation Request
class InvestigationRequestCreate(BaseModel):
    request_type: str = Field(..., description="'laboratory' or 'radiology'")
    test_name: str = Field(..., description="Name of test/imaging requested")
    clinical_history: Optional[str] = None

class InvestigationRequestResponse(BaseModel):
    id: uuid.UUID
    consultation_id: uuid.UUID
    visit_id: uuid.UUID
    patient_id: uuid.UUID
    request_type: str
    test_name: str
    clinical_history: Optional[str]
    status: str
    created_at: datetime
    created_by: Optional[str]

    class Config:
        from_attributes = True

# Consultation
class ConsultationCreate(BaseModel):
    visit_id: uuid.UUID
    patient_id: uuid.UUID
    history_of_presenting_illness: Optional[str] = None
    examination_findings: Optional[str] = None
    clinical_impression: Optional[str] = None

class ConsultationResponse(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    patient_id: uuid.UUID
    history_of_presenting_illness: Optional[str]
    examination_findings: Optional[str]
    clinical_impression: Optional[str]
    disposition: Optional[str] = None
    disposition_notes: Optional[str] = None
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    diagnoses: List[DiagnosisResponse] = []
    investigation_requests: List[InvestigationRequestResponse] = []

    class Config:
        from_attributes = True

# Patient demographics
class PatientResponse(BaseModel):
    id: uuid.UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str
    phone_primary: str
    phone_secondary: Optional[str]
    email: Optional[str]
    address: Optional[str]
    allergies: Optional[str]
    blood_group: Optional[str]
    next_of_kin_name: Optional[str]
    next_of_kin_phone: Optional[str]
    next_of_kin_relationship: Optional[str]

    class Config:
        from_attributes = True

# Triage summary & vitals
class TriageAssessmentResponse(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    patient_id: str
    blood_pressure: Optional[str]
    temperature: Optional[float]
    pulse: Optional[int]
    oxygen_saturation: Optional[float]
    respiratory_rate: Optional[int]
    weight: Optional[float]
    presenting_complaint: Optional[str]
    triage_category: str
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# Patient Encounter View (aggregated)
class EncounterViewResponse(BaseModel):
    patient: PatientResponse
    current_visit_id: uuid.UUID
    triage_summary: Optional[TriageAssessmentResponse] = None
    consultation: Optional[ConsultationResponse] = None

# Patient history timeline item
class VisitHistoryItem(BaseModel):
    visit_id: uuid.UUID
    visit_date: date
    visit_type: str
    status: str
    triage_summary: Optional[TriageAssessmentResponse] = None
    consultation: Optional[ConsultationResponse] = None

# Patient history (timeline)
class PatientHistoryResponse(BaseModel):
    patient: PatientResponse
    previous_visits: List[VisitHistoryItem] = []
