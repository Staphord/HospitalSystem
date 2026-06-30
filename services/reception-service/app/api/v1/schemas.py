"""Pydantic schemas for orchestrated reception endpoints."""

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


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


class PatientSearchResponse(BaseModel):
    patients: list[PatientResponse]
    total: int


class VisitCreateRequest(BaseModel):
    patient_id: str
    visit_type: str
    payment_type: str
    insurer_name: Optional[str] = None
    policy_number: Optional[str] = None

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


class VisitCreateResponse(BaseModel):
    visit: VisitResponse
    queue_number: str
    verification_flag: Optional[str] = None


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


class CombinedVisitData(BaseModel):
    visit_type: str
    payment_type: str
    insurer_name: Optional[str] = None
    policy_number: Optional[str] = None

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
