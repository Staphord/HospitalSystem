# Pydantic schemas for billing

from uuid import UUID
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel

class BillItemOut(BaseModel):
    item_id: UUID | None = None
    bill_id: UUID
    item_code: str = "SERVICE"
    item_type: str = "service"
    description: str
    quantity: Decimal = Decimal("1.0")
    unit_price: Decimal = Decimal("0.0")
    line_total: Decimal = Decimal("0.0")
    created_at: datetime

    class Config:
        from_attributes = True

class BillItemCreate(BaseModel):
    item_code: str = "FEE"
    item_type: str = "service"
    description: str
    quantity: Decimal = Decimal("1.0")
    unit_price: Decimal

class BillAdjustmentIn(BaseModel):
    discount_amount: Decimal
    reason: str

class BillOut(BaseModel):
    bill_id: UUID
    visit_id: UUID | None = None
    admission_id: UUID | None = None
    patient_id: UUID
    patient_name: str | None = None
    patient_number: str | None = None
    status: str
    total_amount: Decimal
    paid_amount: Decimal = Decimal("0.0")
    discount_amount: Decimal = Decimal("0.0")
    currency: str
    created_at: datetime
    updated_at: datetime
    items: list[BillItemOut] = []

    class Config:
        from_attributes = True

class PaymentIn(BaseModel):
    amount: Decimal
    payment_method: str = "Cash"
    notes: str | None = None

class PaymentOut(BaseModel):
    payment_id: UUID
    bill_id: UUID
    amount_paid: Decimal
    payment_method: str
    receipt_number: str
    created_at: datetime
    status: str

    class Config:
        from_attributes = True
