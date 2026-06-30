from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RadiologyReportCreate(BaseModel):
    request_id: Optional[UUID] = None
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
    request_id: Optional[UUID] = None
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
