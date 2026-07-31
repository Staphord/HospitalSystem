from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


# ── Group 1: Request Queue ───────────────────────────────────────────────────

class LabRequestListItem(BaseModel):
    request_id: UUID
    visit_id: UUID
    patient_id: UUID
    patient_name: str
    patient_number: str
    test_name: str
    test_code: Optional[str] = None
    clinical_indication: Optional[str] = None
    urgency: str
    status: str
    requested_by_name: Optional[str] = None
    requested_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LabRequestsListResponse(BaseModel):
    requests: list[LabRequestListItem]


class PatientSummary(BaseModel):
    patient_id: UUID
    patient_number: str
    full_name: str
    date_of_birth: date
    gender: str

    model_config = ConfigDict(from_attributes=True)


class SpecimenSummary(BaseModel):
    specimen_id: UUID
    status: str
    specimen_type: str
    collected_at: datetime
    received_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ResultSummary(BaseModel):
    result_id: UUID
    status: str
    result_value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_critical: bool
    resulted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LabRequestDetailResponse(BaseModel):
    request_id: UUID
    visit_id: UUID
    patient: PatientSummary
    test_name: str
    test_code: Optional[str] = None
    clinical_indication: Optional[str] = None
    urgency: str
    status: str
    requested_by_name: Optional[str] = None
    requested_at: datetime
    specimen: Optional[SpecimenSummary] = None
    result: Optional[ResultSummary] = None

    model_config = ConfigDict(from_attributes=True)


# ── Group 2: Specimen Tracking ───────────────────────────────────────────────

class SpecimenCreateRequest(BaseModel):
    specimen_type: str = Field(..., min_length=1, max_length=100)
    collection_site: Optional[str] = Field(None, max_length=100)
    specimen_label: Optional[str] = Field(None, max_length=50)
    collected_at: datetime


class SpecimenCreateResponse(BaseModel):
    specimen_id: UUID
    request_id: UUID
    status: str
    collected_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpecimenStatusUpdateRequest(BaseModel):
    status: Literal["received", "processing", "completed", "rejected"]
    received_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class SpecimenUpdateResponse(BaseModel):
    specimen_id: UUID
    status: str
    rejection_reason: Optional[str] = None
    request_status: str

    model_config = ConfigDict(from_attributes=True)


class SpecimenAuditItem(BaseModel):
    specimen_id: UUID
    specimen_type: str
    collection_site: Optional[str] = None
    specimen_label: Optional[str] = None
    collected_by_name: Optional[str] = None
    collected_at: datetime
    received_at: Optional[datetime] = None
    status: str
    rejection_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SpecimenListResponse(BaseModel):
    specimens: list[SpecimenAuditItem]


class TrackedSpecimenItem(BaseModel):
    specimen_id: UUID
    request_id: UUID
    patient_id: UUID
    patient_name: str
    patient_number: str
    test_name: str
    urgency: str
    specimen_type: str
    collection_site: Optional[str] = None
    specimen_label: Optional[str] = None
    collected_by_name: Optional[str] = None
    collected_at: datetime
    received_at: Optional[datetime] = None
    status: str
    rejection_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AllSpecimensResponse(BaseModel):
    specimens: list[TrackedSpecimenItem]



# ── Group 3: Results Entry ───────────────────────────────────────────────────

class ResultCreateRequest(BaseModel):
    result_value: str = Field(..., min_length=1)
    unit: Optional[str] = Field(None, max_length=50)
    reference_range: Optional[str] = Field(None, max_length=100)
    is_critical: bool = Field(False)
    result_notes: Optional[str] = None
    specimen_type: Optional[str] = Field(None, max_length=100)
    specimen_label: Optional[str] = Field(None, max_length=50)


class ResultCreateResponse(BaseModel):
    result_id: UUID
    request_id: UUID
    result_value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_critical: bool
    critical_notified_at: Optional[datetime] = None
    status: str
    resulted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResultUpdateRequest(BaseModel):
    result_value: Optional[str] = None
    unit: Optional[str] = Field(None, max_length=50)
    reference_range: Optional[str] = Field(None, max_length=100)
    is_critical: Optional[bool] = None
    result_notes: Optional[str] = None


class ResultUpdateResponse(BaseModel):
    result_id: UUID
    result_value: str
    is_critical: bool
    status: str

    model_config = ConfigDict(from_attributes=True)


class ResultDetailResponse(BaseModel):
    result_id: UUID
    request_id: UUID
    specimen_type: str
    specimen_label: Optional[str] = None
    result_value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_critical: bool
    critical_notified_at: Optional[datetime] = None
    result_notes: Optional[str] = None
    performed_by_name: Optional[str] = None
    verified_by_name: Optional[str] = None
    status: str
    resulted_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Group 4: Result Verification ─────────────────────────────────────────────

class ResultVerifyResponse(BaseModel):
    result_id: UUID
    status: str
    verified_by: str
    request_status: str

    model_config = ConfigDict(from_attributes=True)


# ── Group 5: Billing ─────────────────────────────────────────────────────────

class LabBillCreateRequest(BaseModel):
    unit_price: float = Field(..., gt=0)
    description: str = Field(..., min_length=1, max_length=255)


class LabBillResponse(BaseModel):
    item_id: UUID
    bill_id: UUID
    item_type: str
    description: str
    unit_price: float
    total_price: float
    reference_id: UUID

    model_config = ConfigDict(from_attributes=True)


# ── Group 6: Doctor-Facing Result Read ───────────────────────────────────────

class DoctorVisitResultItem(BaseModel):
    request_id: UUID
    test_name: str
    test_code: Optional[str] = None
    urgency: str
    result_id: UUID
    result_value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_critical: bool
    result_notes: Optional[str] = None
    performed_by_name: Optional[str] = None
    resulted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DoctorVisitResultsResponse(BaseModel):
    visit_id: UUID
    results: list[DoctorVisitResultItem]


# ── Group 7: Dashboard Stats & Turnaround Metrics ────────────────────────────

class StatRequestItem(BaseModel):
    id: UUID
    patient_name: str = Field(..., alias="patientName")
    test_name: str = Field(..., alias="testName")
    requested_by: str = Field(..., alias="requestedBy")
    requested_ago: str = Field(..., alias="requestedAgo")
    priority: str
    status: Optional[str] = "pending"

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CriticalValueItem(BaseModel):
    id: UUID
    patient_name: str = Field(..., alias="patientName")
    test_name: str = Field(..., alias="testName")
    result: str
    ref_range: Optional[str] = Field(None, alias="refRange")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CompletedTestItem(BaseModel):
    id: UUID
    test_name: str = Field(..., alias="testName")
    request_id: str = Field(..., alias="requestId")
    completed_at: str = Field(..., alias="completedAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TurnaroundMetricItem(BaseModel):
    department: str
    minutes: int
    bar_percent: int = Field(..., alias="barPercent")
    is_stat: bool = Field(False, alias="isStat")
    opacity: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class LabDashboardStatsResponse(BaseModel):
    pending_tests: int = Field(..., alias="pendingTests")
    in_progress: int = Field(..., alias="inProgress")
    completed_today: int = Field(..., alias="completedToday")
    critical_values: int = Field(..., alias="criticalValues")
    high_priority_requests: list[StatRequestItem] = Field(default_factory=list, alias="highPriorityRequests")
    critical_values_list: list[CriticalValueItem] = Field(default_factory=list, alias="criticalValuesList")
    completed_today_list: list[CompletedTestItem] = Field(default_factory=list, alias="completedTodayList")
    turnaround_metrics: list[TurnaroundMetricItem] = Field(default_factory=list, alias="turnaroundMetrics")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


