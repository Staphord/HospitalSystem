"""Pydantic schemas for ward-service."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BedOut(BaseModel):
    bed_id: UUID
    ward_name: str
    bed_number: str
    bed_type: str
    is_available: bool
    is_active: bool
    notes: str | None = None

    class Config:
        from_attributes = True


class BedAssignRequest(BaseModel):
    admission_id: UUID | None = None


class AdmissionCreate(BaseModel):
    visit_id: UUID
    bed_id: UUID
    admitting_diagnosis: str = Field(..., min_length=1)


class AdmissionOut(BaseModel):
    admission_id: UUID
    visit_id: UUID
    patient_id: UUID
    bed_id: UUID
    admitting_doctor_id: str
    admitting_diagnosis: str
    admission_date: datetime
    discharge_date: datetime | None = None
    length_of_stay_days: Decimal | None = None
    discharge_diagnosis: str | None = None
    discharge_instructions: str | None = None
    discharge_order_by: str | None = None
    status: str
    ward_name: str | None = None

    class Config:
        from_attributes = True


class DischargeRequest(BaseModel):
    discharge_diagnosis: str = Field(..., min_length=1)
    discharge_instructions: str | None = None


class OrderCreate(BaseModel):
    order_type: str
    order_detail: str = Field(..., min_length=1)
    frequency: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class OrderUpdate(BaseModel):
    order_detail: str | None = None
    frequency: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None


class OrderOut(BaseModel):
    order_id: UUID
    admission_id: UUID
    patient_id: UUID
    order_type: str
    order_detail: str
    frequency: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    ordered_by: str
    status: str
    ordered_at: datetime

    class Config:
        from_attributes = True


class NursingNoteCreate(BaseModel):
    note_type: str
    note_text: str = Field(..., min_length=1)
    vitals_bp: str | None = None
    vitals_temp: Decimal | None = None
    vitals_pulse: int | None = None
    vitals_spo2: Decimal | None = None


class NursingNoteOut(BaseModel):
    note_id: UUID
    admission_id: UUID
    patient_id: UUID
    note_type: str
    note_text: str
    vitals_bp: str | None = None
    vitals_temp: Decimal | None = None
    vitals_pulse: int | None = None
    vitals_spo2: Decimal | None = None
    authored_by: str
    authored_at: datetime

    class Config:
        from_attributes = True


class LosOut(BaseModel):
    admission_id: str
    status: str
    admission_date: datetime
    discharge_date: datetime | None = None
    length_of_stay_days: float
