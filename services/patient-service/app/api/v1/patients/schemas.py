from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class PatientRegisterRequest(BaseModel):
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    national_id: Optional[str] = None
    medical_history: Optional[str] = None
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

    @field_validator("national_id")
    @classmethod
    def validate_national_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            return None
        return v


class PatientResponse(BaseModel):
    id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    national_id: Optional[str] = None
    medical_history: Optional[str] = None
    allergies: Optional[str] = None
    blood_group: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    model_config = {"from_attributes": True}


class PatientSearchResponse(BaseModel):
    patients: list[PatientResponse]
    total: int


class ErrorResponse(BaseModel):
    detail: str
