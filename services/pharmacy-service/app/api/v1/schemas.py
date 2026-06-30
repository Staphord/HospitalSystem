from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Queue ──────────────────────────────────────────────────────────────────────

QueueStatus = Literal["waiting", "in_progress", "completed"]
QueuePriority = Literal["emergency", "urgent", "semi_urgent", "non_urgent"]
PaymentType = Literal["cash", "insurance"]


class PharmacyQueueItem(BaseModel):
    queue_id: UUID
    queue_number: str
    priority: QueuePriority
    status: QueueStatus
    visit_id: UUID
    visit_number: str
    patient_id: UUID
    patient_name: str
    payment_type: PaymentType
    billing_cleared: bool
    prescription_count: int
    called_at: Optional[datetime] = None
    created_at: datetime


class PharmacyQueueResponse(BaseModel):
    date: date
    queue: list[PharmacyQueueItem]


# ── Prescriptions ──────────────────────────────────────────────────────────────

PrescriptionStatus = Literal["pending", "dispensed", "cancelled"]


class DispensingRecordSummary(BaseModel):
    dispensing_id: UUID
    quantity_dispensed: int
    dispensed_at: datetime


class PrescriptionItem(BaseModel):
    prescription_id: UUID
    drug_name: str
    dose: str
    frequency: str
    duration: str
    route: str
    instructions: Optional[str] = None
    prescribed_by: str
    prescribed_at: datetime
    status: PrescriptionStatus
    dispensing_record: Optional[DispensingRecordSummary] = None


class PatientPrescriptionContext(BaseModel):
    patient_id: UUID
    patient_name: str
    date_of_birth: date
    allergies: Optional[str] = None


class VisitPrescriptionsResponse(BaseModel):
    visit_id: UUID
    visit_number: str
    patient: PatientPrescriptionContext
    final_diagnosis: str
    billing_cleared: bool
    prescriptions: list[PrescriptionItem]


# ── Interaction check ──────────────────────────────────────────────────────────

InteractionType = Literal["drug_allergy", "drug_drug"]
InteractionSeverity = Literal["high", "moderate", "low"]


class InteractionAlert(BaseModel):
    type: InteractionType
    severity: InteractionSeverity
    drug_name: Optional[str] = None
    drug_a: Optional[str] = None
    drug_b: Optional[str] = None
    detail: str
    recommendation: str


class InteractionCheckResponse(BaseModel):
    visit_id: UUID
    alerts: list[InteractionAlert]
    alert_count: int
    checked_at: datetime


# ── Dispensing ─────────────────────────────────────────────────────────────────

class DispenseRequest(BaseModel):
    prescription_id: UUID
    visit_id: UUID
    drug_name: str
    batch_number: str
    expiry_date: date
    quantity_dispensed: int = Field(gt=0)
    unit: str
    interaction_alert_acknowledged: bool = False

    @field_validator("expiry_date")
    @classmethod
    def expiry_must_be_future(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("expiry_date must be a future date")
        return v


class DispenseResponse(BaseModel):
    dispensing_id: UUID
    prescription_id: UUID
    drug_name: str
    quantity_dispensed: int
    unit: str
    batch_number: str
    expiry_date: date
    billing_cleared: bool
    dispensed_by: str
    dispensed_at: datetime
    remaining_stock: int
    low_stock_alert_sent: bool
    bill_item_id: UUID


class DispenseSummaryItem(BaseModel):
    prescription_id: UUID
    drug_name: str
    prescribed_dose: str
    prescribed_frequency: Optional[str] = None
    prescription_status: PrescriptionStatus
    dispensing_id: Optional[UUID] = None
    quantity_dispensed: Optional[int] = None
    dispensed_at: Optional[datetime] = None


class DispenseSummaryResponse(BaseModel):
    visit_id: UUID
    patient_name: str
    prescriptions_total: int
    dispensed_count: int
    pending_count: int
    cancelled_count: int
    items: list[DispenseSummaryItem]


# ── Inventory ──────────────────────────────────────────────────────────────────

InventoryTransactionType = Literal["adjustment", "write_off", "return"]


class InventoryListItem(BaseModel):
    inventory_id: UUID
    drug_name: str
    brand_name: Optional[str] = None
    drug_code: Optional[str] = None
    category: Optional[str] = None
    unit: str
    quantity_in_stock: int
    reorder_level: int
    is_low_stock: bool
    unit_cost: float
    unit_price: float
    location: Optional[str] = None
    last_restocked_at: Optional[datetime] = None


class InventoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[InventoryListItem]


class InventoryTransactionItem(BaseModel):
    transaction_id: UUID
    transaction_type: str
    quantity_change: int
    quantity_before: int
    quantity_after: int
    performed_by: str
    created_at: datetime


class InventoryDetailResponse(InventoryListItem):
    recent_transactions: list[InventoryTransactionItem]


class RestockRequest(BaseModel):
    inventory_id: UUID
    quantity_added: int = Field(gt=0)
    batch_number: str
    expiry_date: date
    unit_cost: float = Field(ge=0)
    notes: Optional[str] = None

    @field_validator("expiry_date")
    @classmethod
    def expiry_must_be_future(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("expiry_date must be a future date")
        return v


class RestockResponse(BaseModel):
    inventory_id: UUID
    drug_name: str
    quantity_added: int
    quantity_before: int
    quantity_after: int
    batch_number: str
    transaction_id: UUID
    restocked_by: str
    restocked_at: datetime


class AdjustInventoryRequest(BaseModel):
    inventory_id: UUID
    transaction_type: InventoryTransactionType
    quantity_change: int
    notes: str = Field(min_length=1)


class AdjustInventoryResponse(BaseModel):
    inventory_id: UUID
    drug_name: str
    quantity_in_stock: int
    transaction_id: UUID
    transaction_type: InventoryTransactionType
    quantity_change: int
    notes: str
    adjusted_by: str
    adjusted_at: datetime


class LowStockAlertItem(BaseModel):
    inventory_id: UUID
    drug_name: str
    quantity_in_stock: int
    reorder_level: int
    unit: str
    shortage_gap: int
    last_restocked_at: Optional[datetime] = None


class LowStockAlertsResponse(BaseModel):
    alert_count: int
    alerts: list[LowStockAlertItem]


# ── Labels ─────────────────────────────────────────────────────────────────────

class LabelGenerateRequest(BaseModel):
    dispensing_id: UUID


class LabelPayload(BaseModel):
    patient_name: str
    drug_name: str
    dose: str
    frequency: str
    duration: str
    route: str
    instructions: Optional[str] = None
    dispensed_date: str
    dispensed_by: str
    batch_number: str
    expiry_date: str


class LabelGenerateResponse(BaseModel):
    label: LabelPayload


# ── Notifications ──────────────────────────────────────────────────────────────

class PharmacyNotificationItem(BaseModel):
    notification_id: UUID
    notification_type: str
    title: str
    message: str
    reference_type: Optional[str] = None
    reference_id: Optional[UUID] = None
    created_at: datetime


class PharmacyNotificationsResponse(BaseModel):
    unread_count: int
    notifications: list[PharmacyNotificationItem]


class MarkNotificationReadResponse(BaseModel):
    marked_read: bool
