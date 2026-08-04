from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Report schemas ──────────────────────────────────────────────────────────────

class RadiologyReportCreate(BaseModel):
    request_id: UUID
    visit_id: UUID
    patient_id: UUID
    modality: str = Field(description="xray / ct / mri / ultrasound / fluoroscopy / mammography / other")
    body_part: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    performed_at: Optional[datetime] = None
    findings: Optional[str] = None
    impression: Optional[str] = None
    image_reference: Optional[str] = None
    performed_by: UUID
    reported_by: Optional[UUID] = None
    status: str = "scheduled"


class RadiologyReportUpdate(BaseModel):
    modality: Optional[str] = None
    body_part: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    performed_at: Optional[datetime] = None
    findings: Optional[str] = None
    impression: Optional[str] = None
    image_reference: Optional[str] = None
    performed_by: Optional[UUID] = None
    reported_by: Optional[UUID] = None
    status: Optional[str] = None


class RadiologyReportResponse(BaseModel):
    report_id: UUID
    request_id: UUID
    visit_id: UUID
    patient_id: UUID
    modality: str
    body_part: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    performed_at: Optional[datetime] = None
    findings: Optional[str] = None
    impression: Optional[str] = None
    image_reference: Optional[str] = None
    performed_by: UUID
    reported_by: Optional[UUID] = None
    status: str
    reported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RadiologyReportListResponse(BaseModel):
    reports: list[RadiologyReportResponse]
    total: int


# ── Request worklist schemas ────────────────────────────────────────────────────

class PatientSummary(BaseModel):
    patient_id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str] = None
    allergies: Optional[str] = None


class VisitSummary(BaseModel):
    visit_id: UUID
    visit_number: str
    visit_date: date
    visit_type: str
    payment_type: str


class ImagingRequestListItem(BaseModel):
    request_id: UUID
    test_name: str
    clinical_indication: Optional[str] = None
    urgency: str
    status: str
    requested_by: Optional[str] = None
    requested_at: datetime
    patient_name: str
    patient_number: str
    visit_id: UUID
    visit_number: str
    report_id: Optional[UUID] = None
    report_status: Optional[str] = None
    modality: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class ImagingRequestListResponse(BaseModel):
    requests: list[ImagingRequestListItem]
    total: int


class ImagingRequestDetailResponse(BaseModel):
    request_id: UUID
    test_name: str
    request_type: str
    clinical_indication: Optional[str] = None
    urgency: str
    requested_by: Optional[str] = None
    requested_at: datetime
    status: str
    patient: PatientSummary
    visit: VisitSummary
    report: Optional[RadiologyReportResponse] = None


# ── Workflow action schemas ─────────────────────────────────────────────────────

class ScheduleImagingRequest(BaseModel):
    modality: str = Field(description="xray / ct / mri / ultrasound / fluoroscopy / mammography / other")
    body_part: Optional[str] = None
    scheduled_at: datetime
    performed_by: Optional[UUID] = None


class PerformImagingRequest(BaseModel):
    performed_at: Optional[datetime] = None
    performed_by: Optional[UUID] = None


class EnterReportRequest(BaseModel):
    findings: str
    impression: str
    image_reference: Optional[str] = None
    reported_by: Optional[UUID] = None
