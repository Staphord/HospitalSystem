from pydantic import BaseModel
from datetime import datetime
from typing import Any


class VisitCreatedPayload(BaseModel):
    visit_id: str
    patient_id: str
    tenant_id: str
    created_at: datetime


class TriageCompletedPayload(BaseModel):
    triage_id: str
    visit_id: str
    tenant_id: str
    category: str
    priority: int


class InvestigationRequestedPayload(BaseModel):
    request_id: str
    visit_id: str
    tenant_id: str
    investigation_type: str  # "laboratory" or "radiology"
    tests: list[str]


class PrescriptionIssuedPayload(BaseModel):
    prescription_id: str
    visit_id: str
    tenant_id: str
    drugs: list[dict[str, Any]]


class LabResultReadyPayload(BaseModel):
    result_id: str
    request_id: str
    tenant_id: str
    status: str


class LabCriticalValuePayload(BaseModel):
    result_id: str
    patient_id: str
    tenant_id: str
    value: str
    test_name: str


class RadiologyReportReadyPayload(BaseModel):
    report_id: str
    request_id: str
    tenant_id: str
    status: str


class DrugDispensedPayload(BaseModel):
    dispensing_id: str
    prescription_id: str
    tenant_id: str
    drugs: list[dict[str, Any]]


class StockLowPayload(BaseModel):
    inventory_id: str
    tenant_id: str
    drug_name: str
    current_quantity: int
    threshold: int


class PatientAdmittedPayload(BaseModel):
    admission_id: str
    patient_id: str
    tenant_id: str
    bed_id: str


class PatientDischargedPayload(BaseModel):
    admission_id: str
    patient_id: str
    tenant_id: str
    discharge_date: datetime


class PaymentReceivedPayload(BaseModel):
    payment_id: str
    bill_id: str
    tenant_id: str
    amount: float
    method: str


class TenantCreatedPayload(BaseModel):
    tenant_id: str
    name: str
    admin_email: str


class TenantSuspendedPayload(BaseModel):
    tenant_id: str
    reason: str
