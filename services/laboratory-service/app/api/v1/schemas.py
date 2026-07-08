from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


# ── Queue & Worklist ────────────────────────────────────────────────────────────

class LabQueueItem(BaseModel):
    queue_id: UUID
    request_id: UUID
    patient_id: UUID
    patient_name: str
    test_name: str
    urgency: str
    status: str
    requested_at: datetime
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class LabQueueResponse(BaseModel):
    date: date
    queue: list[LabQueueItem]

    model_config = ConfigDict(from_attributes=True)


# ── Request Detail ─────────────────────────────────────────────────────────────

class PatientDemographics(BaseModel):
    patient_id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    allergies: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class VisitContext(BaseModel):
    visit_id: UUID
    visit_number: str
    visit_date: date
    visit_type: str
    payment_type: str

    model_config = ConfigDict(from_attributes=True)


class LabRequestDetailResponse(BaseModel):
    request_id: UUID
    test_name: str
    request_type: str
    clinical_indication: Optional[str] = None
    urgency: str
    requested_by: Optional[str] = None
    requested_at: datetime
    status: str
    patient: PatientDemographics
    visit: VisitContext

    model_config = ConfigDict(from_attributes=True)


# ── Specimen Management ────────────────────────────────────────────────────────

class SpecimenCreateRequest(BaseModel):
    specimen_type: str = Field(..., min_length=1, max_length=100)
    collection_site: Optional[str] = Field(None, max_length=100)


class SpecimenResponse(BaseModel):
    specimen_id: UUID
    request_id: UUID
    patient_id: UUID
    specimen_type: str
    collection_site: Optional[str]
    collected_by: str
    collected_at: datetime
    received_at: Optional[datetime]
    status: str
    rejection_reason: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpecimenStatusRequest(BaseModel):
    status: Literal["processing", "completed"]


class SpecimenRejectRequest(BaseModel):
    rejection_reason: str = Field(..., min_length=3)


# ── Results Entry ──────────────────────────────────────────────────────────────

class ResultCreateRequest(BaseModel):
    specimen_type: str = Field(..., min_length=1, max_length=100)
    result_value: str = Field(...)
    unit: Optional[str] = Field(None, max_length=50)
    reference_range: Optional[str] = Field(None, max_length=100)
    is_critical: bool = Field(False)
    result_notes: Optional[str] = None


class ResultUpdateRequest(BaseModel):
    result_value: str = Field(...)
    unit: Optional[str] = Field(None, max_length=50)
    reference_range: Optional[str] = Field(None, max_length=100)
    is_critical: bool = Field(False)
    result_notes: Optional[str] = None


class ResultResponse(BaseModel):
    result_id: UUID
    request_id: UUID
    visit_id: UUID
    patient_id: UUID
    specimen_type: str
    result_value: str
    unit: Optional[str]
    reference_range: Optional[str]
    is_critical: bool
    result_notes: Optional[str]
    performed_by: str
    verified_by: Optional[str]
    status: str
    resulted_at: datetime
    critical_notified_at: Optional[datetime]
    verified_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PatientResultsResponse(BaseModel):
    patient_id: UUID
    results: list[ResultResponse]

    model_config = ConfigDict(from_attributes=True)
