import uuid
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# Diagnosis
class DiagnosisCreate(BaseModel):
    diagnosis_type: str = Field(..., description="'provisional', 'differential', or 'final'")
    code: Optional[str] = Field(None, description="Optional ICD-10 code (mapped to code/icd10_code)")
    description: str = Field(..., description="Diagnosis text description")
    sequence_order: Optional[int] = Field(None, description="Required when diagnosis_type = 'differential'")

class DiagnosisResponse(BaseModel):
    id: uuid.UUID
    consultation_id: uuid.UUID
    diagnosis_type: str
    code: Optional[str]
    description: str
    sequence_order: Optional[int]
    recorded_by: Optional[str]
    recorded_at: datetime

    class Config:
        from_attributes = True

class DiagnosisUpdateRequest(BaseModel):
    icd10_code: Optional[str] = Field(None, description="Optional ICD-10 code")
    diagnosis_text: str = Field(..., description="Diagnosis text description")
    sequence_order: Optional[int] = Field(None, description="Differential order")

# Prescription
class PrescriptionCreate(BaseModel):
    drug_name: str = Field(..., description="Name of drug")
    dose: str = Field(..., description="e.g. 500mg")
    frequency: str = Field(..., description="e.g. twice daily")
    duration: str = Field(..., description="e.g. 7 days")
    route: str = Field(..., description="oral / iv / im / topical / inhaled / sublingual / other")
    instructions: Optional[str] = Field(None, description="dispensing instructions")

class PrescriptionResponse(BaseModel):
    prescription_id: uuid.UUID = Field(..., alias="id")
    visit_id: uuid.UUID
    consultation_id: uuid.UUID
    patient_id: uuid.UUID
    drug_name: str
    dose: str
    frequency: str
    duration: str
    route: str
    instructions: Optional[str]
    prescribed_by: Optional[str]
    status: str
    prescribed_at: datetime
    dispensing_status: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True

# Investigation Request
class InvestigationRequestCreate(BaseModel):
    request_type: str = Field(..., description="'lab' or 'radiology'")
    test_name: str = Field(..., description="Name of test")
    test_code: Optional[str] = Field(None, description="Optional test code")
    clinical_indication: str = Field(..., description="Reason for requesting test")
    urgency: str = Field("routine", description="'routine', 'urgent', or 'stat'")

class InvestigationRequestResponse(BaseModel):
    request_id: uuid.UUID = Field(..., alias="id")
    visit_id: uuid.UUID
    consultation_id: uuid.UUID
    patient_id: uuid.UUID
    request_type: str
    test_name: str
    test_code: Optional[str]
    clinical_history: Optional[str] = Field(None, alias="clinical_history")
    status: str
    urgency: str
    requested_by: Optional[str] = Field(None, alias="created_by")
    requested_at: Optional[datetime] = Field(None, alias="created_at")
    result: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
        populate_by_name = True

# Triage summary & vitals
class TriageAssessmentResponse(BaseModel):
    triage_id: uuid.UUID
    visit_id: uuid.UUID
    patient_id: uuid.UUID
    triage_nurse_id: Optional[uuid.UUID]
    blood_pressure_systolic: Optional[int]
    blood_pressure_diastolic: Optional[int]
    temperature: Optional[float]
    pulse_rate: Optional[int]
    oxygen_saturation: Optional[float]
    respiratory_rate: Optional[int]
    weight_kg: Optional[float]
    chief_complaint: str
    complaint_code: Optional[str]
    triage_category: str
    triage_notes: Optional[str]
    assessed_at: datetime
    created_at: datetime

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
    consultation_status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    disposition: Optional[str] = None
    referral_type: Optional[str] = None
    referral_notes: Optional[str] = None
    admission_reason: Optional[str] = None
    discharge_instructions: Optional[str] = None
    follow_up_date: Optional[date] = None
    return_date: Optional[date] = None
    return_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    diagnoses: List[DiagnosisResponse] = []
    investigation_requests: List[InvestigationRequestResponse] = []
    prescriptions: List[PrescriptionResponse] = []

    class Config:
        from_attributes = True

class NotesUpdateRequest(BaseModel):
    presenting_history: str = Field(..., description="history of presenting illness")
    examination_findings: str = Field(..., description="clinical examination findings")
    clinical_impression: str = Field(..., description="clinical impression")

class DispositionUpdateRequest(BaseModel):
    disposition: str = Field(..., description="'outpatient' / 'admission' / 'referral' / 'return_visit' / 'deceased'")
    referral_type: Optional[str] = Field(None, description="required if disposition = 'referral'")
    referral_notes: Optional[str] = Field(None, description="required if disposition = 'referral'")
    admission_reason: Optional[str] = Field(None, description="required if disposition = 'admission'")
    discharge_instructions: Optional[str] = Field(None, description="optional instructions for discharge")
    follow_up_date: Optional[str] = Field(None, description="optional follow-up date (YYYY-MM-DD) for discharge")
    return_date: Optional[str] = Field(None, description="required if disposition = 'return_visit' (YYYY-MM-DD)")
    return_reason: Optional[str] = Field(None, description="required if disposition = 'return_visit'")

class ConsultationCompleteResponse(BaseModel):
    consultation_id: uuid.UUID
    consultation_status: str
    disposition: Optional[str]
    completed_at: datetime
    visit_status: str

# Queue Item Response
class QueueItemResponse(BaseModel):
    queue_id: uuid.UUID
    queue_number: str
    priority: str
    visit_id: uuid.UUID
    visit_number: str
    patient_id: uuid.UUID
    full_name: str
    patient_number: str
    age: int
    triage_category: Optional[str]
    chief_complaint: Optional[str]
    wait_time_minutes: int
    queue_status: str
    visit_status: Optional[str] = None
    pending_investigations_count: int = 0
    completed_investigations_count: int = 0

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

class EncounterOpenResponse(BaseModel):
    consultation_id: uuid.UUID
    visit_id: uuid.UUID
    patient: PatientResponse
    triage_summary: Optional[TriageAssessmentResponse] = None
    visit_history: List[VisitHistoryItem] = []
    bills_bill_id: Optional[uuid.UUID] = None
    consultation_status: str
    started_at: datetime

