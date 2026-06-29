from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


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
            UUID(v)
        except ValueError:
            raise ValueError("patient_id must be a valid UUID")
        return v.strip()


class VisitStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"triaged", "in_consultation", "in_lab", "in_pharmacy", "completed", "cancelled"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.lower()


class VisitResponse(BaseModel):
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
    registered_by: UUID
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
    patient_id: UUID
    queue_type: str
    queue_number: str
    priority: str
    status: str
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QueueAddRequest(BaseModel):
    visit_id: str
    patient_id: str
    queue_type: str
    priority: str = "non_urgent"

    @field_validator("queue_type")
    @classmethod
    def validate_queue_type(cls, v: str) -> str:
        allowed = {"triage", "doctor", "lab", "radiology", "pharmacy", "billing"}
        if v.lower() not in allowed:
            raise ValueError(f"queue_type must be one of {allowed}")
        return v.lower()

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        allowed = {"emergency", "urgent", "semi_urgent", "non_urgent"}
        if v.lower() not in allowed:
            raise ValueError(f"priority must be one of {allowed}")
        return v.lower()


class QueueStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"in_progress", "completed", "skipped"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.lower()


class QueueCallResponse(BaseModel):
    queue_id: UUID
    visit_id: UUID
    patient_id: UUID
    queue_type: str
    queue_number: str
    priority: str
    status: str
    called_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class QueueListResponse(BaseModel):
    queue_id: UUID
    visit_id: UUID
    patient_id: UUID
    queue_type: str
    queue_number: str
    priority: str
    status: str
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    detail: str
