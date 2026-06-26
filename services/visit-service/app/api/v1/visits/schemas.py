from datetime import date, datetime
from typing import Optional
import uuid
from uuid import UUID

from pydantic import BaseModel, field_validator


class TriageCompleteRequest(BaseModel):
    priority: str

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        allowed = {"emergency", "urgent", "semi_urgent", "non_urgent"}
        if v.lower() not in allowed:
            raise ValueError(f"priority must be one of {allowed}")
        return v.lower()



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

    @field_validator("patient_id")
    @classmethod
    def validate_patient_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("patient_id cannot be empty")
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("patient_id must be a valid UUID")
        return v.strip()


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


class ErrorResponse(BaseModel):
    detail: str
