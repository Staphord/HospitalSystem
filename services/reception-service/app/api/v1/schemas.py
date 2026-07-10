"""Pydantic schemas for orchestrated reception endpoints."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Patient schemas
# ---------------------------------------------------------------------------

class PatientRegisterRequest(BaseModel):
    full_name: str
    date_of_birth: date
    gender: str
    phone_primary: str
    phone_secondary: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    next_of_kin_name: Optional[str] = None
    next_of_kin_phone: Optional[str] = None
    next_of_kin_relationship: Optional[str] = None
    national_id: Optional[str] = None
    allergies: Optional[str] = None
    blood_group: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name cannot be empty")
        return v.strip()

    @field_validator("date_of_birth")
    @classmethod
    def dob_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        allowed = {"male", "female", "other"}
        if v.lower() not in allowed:
            raise ValueError(f"gender must be one of {allowed}")
        return v.lower()


class PatientResponse(BaseModel):
    id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str
    phone_primary: Optional[str] = None
    phone_secondary: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    next_of_kin_name: Optional[str] = None
    next_of_kin_phone: Optional[str] = None
    next_of_kin_relationship: Optional[str] = None
    national_id: Optional[str] = None
    allergies: Optional[str] = None
    blood_group: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    model_config = {"from_attributes": True}


class PatientSummary(BaseModel):
    """Minimal patient info embedded in visit/queue responses."""
    patient_id: UUID
    patient_number: str
    full_name: str


class PatientSearchResponse(BaseModel):
    patients: list[PatientResponse]
    total: int
    page: int = 1
    page_size: int = 20


# ---------------------------------------------------------------------------
# Insurance schemas
# ---------------------------------------------------------------------------

class InsurancePolicyCreateRequest(BaseModel):
    insurer_name: str
    policy_number: str
    coverage_limit: Optional[Decimal] = None
    expiry_date: Optional[date] = None

    @field_validator("insurer_name")
    @classmethod
    def insurer_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("insurer_name cannot be empty")
        return v.strip()

    @field_validator("policy_number")
    @classmethod
    def policy_number_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("policy_number cannot be empty")
        return v.strip()


class InsurancePolicyResponse(BaseModel):
    insurance_id: UUID
    patient_id: UUID
    insurer_name: str
    policy_number: str
    coverage_limit: Optional[Decimal] = None
    expiry_date: Optional[date] = None
    verification_status: str
    verified_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class InsuranceSummary(BaseModel):
    """Minimal insurance info embedded in visit detail response."""
    insurance_id: UUID
    insurer_name: str
    policy_number: str
    verification_status: str


class InsuranceVerifyRequest(BaseModel):
    verification_status: str

    @field_validator("verification_status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"verified", "rejected"}
        if v.lower() not in allowed:
            raise ValueError(f"verification_status must be one of {allowed}")
        return v.lower()


# ---------------------------------------------------------------------------
# Visit schemas
# ---------------------------------------------------------------------------

class VisitCreateRequest(BaseModel):
    patient_id: str
    visit_type: str
    payment_type: str
    insurance_id: Optional[UUID] = None   # Required if payment_type = "insurance"

    @field_validator("visit_type")
    @classmethod
    def validate_visit_type(cls, v: str) -> str:
        allowed = {"outpatient", "inpatient", "emergency"}
        if v.lower() not in allowed:
            raise ValueError(f"visit_type must be one of {allowed}")
        return v.lower()

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, v: str) -> str:
        allowed = {"cash", "insurance"}
        if v.lower() not in allowed:
            raise ValueError(f"payment_type must be one of {allowed}")
        return v.lower()


class VisitResponse(BaseModel):
    visit_id: UUID
    patient_id: str
    visit_number: str
    visit_date: date
    visit_type: str
    payment_type: str
    insurance_id: Optional[UUID] = None
    verification_flag: Optional[str] = None
    queue_number: Optional[str] = None
    status: str
    registered_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class QueueSummary(BaseModel):
    queue_id: UUID
    visit_id: UUID
    patient_id: UUID
    queue_type: str
    queue_number: str
    priority: str
    status: str
    created_at: datetime


class VisitCreateResponse(BaseModel):
    visit: VisitResponse
    queue: QueueSummary
    queue_number: str            # kept for backwards compat
    verification_flag: Optional[str] = None


class VisitSummary(BaseModel):
    """Minimal visit info embedded in queue entry responses."""
    visit_id: UUID
    visit_number: str
    queue_number: Optional[str] = None
    visit_type: str
    payment_type: str
    status: str


class VisitDetailResponse(BaseModel):
    """Full visit detail with nested patient and optional insurance."""
    visit_id: UUID
    patient_id: UUID
    visit_number: str
    visit_date: date
    visit_type: str
    payment_type: str
    insurance_id: Optional[UUID] = None
    verification_flag: Optional[str] = None
    queue_number: Optional[str] = None
    status: str
    registered_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    patient: Optional[PatientSummary] = None
    insurance: Optional[InsuranceSummary] = None


class QueueTodayResponse(BaseModel):
    queue_id: UUID
    visit_id: UUID
    patient_id: str
    queue_type: str
    queue_number: str
    priority: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class QueueEntryWithContext(BaseModel):
    """Queue entry enriched with patient and visit context for reception worklist."""
    queue_id: UUID
    queue_number: str
    queue_type: str
    priority: str
    status: str
    created_at: datetime
    patient: PatientSummary
    visit: VisitSummary


# ---------------------------------------------------------------------------
# Combined register-and-visit (existing, kept for backward compat)
# ---------------------------------------------------------------------------

class CombinedVisitData(BaseModel):
    visit_type: str
    payment_type: str
    insurance_id: Optional[UUID] = None

    @field_validator("visit_type")
    @classmethod
    def validate_visit_type(cls, v: str) -> str:
        allowed = {"outpatient", "inpatient", "emergency"}
        if v.lower() not in allowed:
            raise ValueError(f"visit_type must be one of {allowed}")
        return v.lower()

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, v: str) -> str:
        allowed = {"cash", "insurance"}
        if v.lower() not in allowed:
            raise ValueError(f"payment_type must be one of {allowed}")
        return v.lower()


class CombinedRegisterAndVisitRequest(BaseModel):
    patient: PatientRegisterRequest
    visit: CombinedVisitData


class CombinedRegisterAndVisitResponse(BaseModel):
    patient: PatientResponse
    visit: VisitCreateResponse


class ErrorResponse(BaseModel):
    detail: str
