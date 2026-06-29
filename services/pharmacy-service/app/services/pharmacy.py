"""
Pharmacy business logic.

Phase 1: stub implementations returning spec-shaped responses.
Replace with real DB / cross-service logic in later phases.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from app.api.v1.schemas import (
    AdjustInventoryRequest,
    AdjustInventoryResponse,
    DispenseRequest,
    DispenseResponse,
    DispenseSummaryResponse,
    InteractionCheckResponse,
    InventoryDetailResponse,
    InventoryListResponse,
    InventoryTransactionItem,
    LabelGenerateRequest,
    LabelGenerateResponse,
    LowStockAlertsResponse,
    MarkNotificationReadResponse,
    PharmacyNotificationsResponse,
    PharmacyQueueItem,
    PharmacyQueueResponse,
    RestockRequest,
    RestockResponse,
    VisitPrescriptionsResponse,
)
from app.core.security import TokenPayload
from app.exceptions import ConflictError, NotFoundError

# ── Stub reference IDs (stable for PATCH / GET by id during Phase 1) ───────────

STUB_QUEUE_ID = UUID("a1000001-0001-4001-8001-000000000001")
STUB_QUEUE_COMPLETED_ID = UUID("a1000001-0001-4001-8001-000000000099")
STUB_VISIT_ID = UUID("b2000002-0002-4002-8002-000000000002")
STUB_PATIENT_ID = UUID("c3000003-0003-4003-8003-000000000003")
STUB_PRESCRIPTION_PENDING_ID = UUID("d4000004-0004-4004-8004-000000000004")
STUB_PRESCRIPTION_DISPENSED_ID = UUID("d4000004-0004-4004-8004-000000000005")
STUB_INVENTORY_ID = UUID("e5000005-0005-4005-8005-000000000005")
STUB_DISPENSING_ID = UUID("f6000006-0006-4006-8006-000000000006")
STUB_NOTIFICATION_ID = UUID("a7000007-0007-4007-8007-000000000007")
STUB_VISIT_WITH_ALERTS_ID = UUID("b2000002-0002-4002-8002-000000000088")

_NOW = datetime.now(timezone.utc)


def _pharmacist_name(user: TokenPayload) -> str:
    return user.preferred_username or user.email or "Pharmacist"


# ── Queue ──────────────────────────────────────────────────────────────────────

def get_pharmacy_queue(
    queue_date: date,
    status: str,
) -> PharmacyQueueResponse:
    if status not in ("waiting", "in_progress", "completed"):
        status = "waiting"

    items: list[PharmacyQueueItem] = []
    if status in ("waiting", "in_progress"):
        items.append(
            PharmacyQueueItem(
                queue_id=STUB_QUEUE_ID,
                queue_number="PH-007",
                priority="urgent",
                status="in_progress" if status == "in_progress" else "waiting",
                visit_id=STUB_VISIT_ID,
                visit_number="V-20260315-042",
                patient_id=STUB_PATIENT_ID,
                patient_name="Jane Mwita",
                payment_type="cash",
                billing_cleared=True,
                prescription_count=3,
                called_at=_NOW if status == "in_progress" else None,
                created_at=_NOW,
            )
        )
    return PharmacyQueueResponse(date=queue_date, queue=items)


def call_queue_patient(queue_id: UUID, user: TokenPayload) -> PharmacyQueueItem:
    if queue_id == STUB_QUEUE_COMPLETED_ID:
        raise ConflictError("Queue entry is already completed")
    if queue_id != STUB_QUEUE_ID:
        raise NotFoundError("Queue entry not found")

    return PharmacyQueueItem(
        queue_id=STUB_QUEUE_ID,
        queue_number="PH-007",
        priority="urgent",
        status="in_progress",
        visit_id=STUB_VISIT_ID,
        visit_number="V-20260315-042",
        patient_id=STUB_PATIENT_ID,
        patient_name="Jane Mwita",
        payment_type="cash",
        billing_cleared=True,
        prescription_count=3,
        called_at=_NOW,
        created_at=_NOW,
    )


# ── Prescriptions ──────────────────────────────────────────────────────────────

def get_visit_prescriptions(visit_id: UUID) -> VisitPrescriptionsResponse:
    if visit_id != STUB_VISIT_ID:
        raise NotFoundError("Visit not found")

    return VisitPrescriptionsResponse(
        visit_id=STUB_VISIT_ID,
        visit_number="V-20260315-042",
        patient={
            "patient_id": STUB_PATIENT_ID,
            "patient_name": "Jane Mwita",
            "date_of_birth": date(1985, 4, 12),
            "allergies": "Penicillin, Sulfonamides",
        },
        final_diagnosis="Bacterial Pneumonia (J18.9)",
        billing_cleared=True,
        prescriptions=[
            {
                "prescription_id": STUB_PRESCRIPTION_PENDING_ID,
                "drug_name": "Amoxicillin",
                "dose": "500mg",
                "frequency": "Three times daily",
                "duration": "7 days",
                "route": "oral",
                "instructions": "Take after meals",
                "prescribed_by": "Dr. Nguyen",
                "prescribed_at": _NOW,
                "status": "pending",
                "dispensing_record": None,
            },
            {
                "prescription_id": STUB_PRESCRIPTION_DISPENSED_ID,
                "drug_name": "Ibuprofen",
                "dose": "400mg",
                "frequency": "As needed",
                "duration": "5 days",
                "route": "oral",
                "instructions": None,
                "prescribed_by": "Dr. Nguyen",
                "prescribed_at": _NOW,
                "status": "dispensed",
                "dispensing_record": {
                    "dispensing_id": STUB_DISPENSING_ID,
                    "quantity_dispensed": 10,
                    "dispensed_at": _NOW,
                },
            },
        ],
    )


def check_drug_interactions(visit_id: UUID) -> InteractionCheckResponse:
    if visit_id not in (STUB_VISIT_ID, STUB_VISIT_WITH_ALERTS_ID):
        raise NotFoundError("Visit not found")

    alerts = []
    if visit_id == STUB_VISIT_WITH_ALERTS_ID or visit_id == STUB_VISIT_ID:
        alerts = [
            {
                "type": "drug_allergy",
                "severity": "high",
                "drug_name": "Amoxicillin",
                "detail": (
                    "Patient has documented Penicillin allergy. "
                    "Amoxicillin is a penicillin-class antibiotic."
                ),
                "recommendation": "Confirm with prescribing doctor before dispensing.",
            },
        ]

    return InteractionCheckResponse(
        visit_id=visit_id,
        alerts=alerts,
        alert_count=len(alerts),
        checked_at=_NOW,
    )


# ── Dispensing ─────────────────────────────────────────────────────────────────

def dispense_prescription(body: DispenseRequest, user: TokenPayload) -> DispenseResponse:
    if body.visit_id != STUB_VISIT_ID:
        raise NotFoundError("Prescription not found")

    if body.prescription_id == STUB_PRESCRIPTION_DISPENSED_ID:
        raise ConflictError("ALREADY_DISPENSED")

    if body.prescription_id != STUB_PRESCRIPTION_PENDING_ID:
        raise NotFoundError("PRESCRIPTION_NOT_FOUND")

    alerts = check_drug_interactions(body.visit_id)
    if alerts.alert_count > 0 and not body.interaction_alert_acknowledged:
        raise ConflictError("INTERACTION_ALERT_NOT_ACKNOWLEDGED")

    return DispenseResponse(
        dispensing_id=uuid4(),
        prescription_id=body.prescription_id,
        drug_name=body.drug_name,
        quantity_dispensed=body.quantity_dispensed,
        unit=body.unit,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        billing_cleared=True,
        dispensed_by=_pharmacist_name(user),
        dispensed_at=_NOW,
        remaining_stock=179,
        low_stock_alert_sent=False,
        bill_item_id=uuid4(),
    )


def get_dispense_summary(visit_id: UUID) -> DispenseSummaryResponse:
    if visit_id != STUB_VISIT_ID:
        raise NotFoundError("Visit not found")

    return DispenseSummaryResponse(
        visit_id=STUB_VISIT_ID,
        patient_name="Jane Mwita",
        prescriptions_total=3,
        dispensed_count=2,
        pending_count=1,
        cancelled_count=0,
        items=[
            {
                "prescription_id": STUB_PRESCRIPTION_DISPENSED_ID,
                "drug_name": "Amoxicillin",
                "prescribed_dose": "500mg",
                "prescribed_frequency": "Three times daily",
                "prescription_status": "dispensed",
                "dispensing_id": STUB_DISPENSING_ID,
                "quantity_dispensed": 21,
                "dispensed_at": _NOW,
            },
            {
                "prescription_id": STUB_PRESCRIPTION_PENDING_ID,
                "drug_name": "Ibuprofen",
                "prescribed_dose": "400mg",
                "prescription_status": "pending",
                "dispensing_id": None,
                "quantity_dispensed": None,
                "dispensed_at": None,
            },
        ],
    )


# ── Inventory ──────────────────────────────────────────────────────────────────

def _stub_inventory_item() -> dict:
    return {
        "inventory_id": STUB_INVENTORY_ID,
        "drug_name": "Amoxicillin",
        "brand_name": "Amoxil",
        "drug_code": "AMX-500",
        "category": "Antibiotic",
        "unit": "tablets",
        "quantity_in_stock": 179,
        "reorder_level": 100,
        "is_low_stock": False,
        "unit_cost": 50.00,
        "unit_price": 80.00,
        "location": "Shelf B-3",
        "last_restocked_at": _NOW,
    }


def list_inventory(
    search: str | None,
    category: str | None,
    low_stock: bool | None,
    page: int,
    page_size: int,
) -> InventoryListResponse:
    item = _stub_inventory_item()
    items = [item]

    if search:
        needle = search.lower()
        if needle not in item["drug_name"].lower() and needle not in (item["brand_name"] or "").lower():
            items = []
    if category and item.get("category", "").lower() != category.lower():
        items = []
    if low_stock is True and not item["is_low_stock"]:
        items = []
    if low_stock is False and item["is_low_stock"]:
        items = []

    return InventoryListResponse(
        total=len(items),
        page=page,
        page_size=page_size,
        items=items,
    )


def get_inventory_detail(inventory_id: UUID, user: TokenPayload) -> InventoryDetailResponse:
    if inventory_id != STUB_INVENTORY_ID:
        raise NotFoundError("Inventory item not found")

    return InventoryDetailResponse(
        **_stub_inventory_item(),
        recent_transactions=[
            InventoryTransactionItem(
                transaction_id=uuid4(),
                transaction_type="dispensing",
                quantity_change=-21,
                quantity_before=200,
                quantity_after=179,
                performed_by=_pharmacist_name(user),
                created_at=_NOW,
            ),
        ],
    )


def restock_inventory(body: RestockRequest, user: TokenPayload) -> RestockResponse:
    if body.inventory_id != STUB_INVENTORY_ID:
        raise NotFoundError("Inventory item not found")

    quantity_before = 179
    return RestockResponse(
        inventory_id=body.inventory_id,
        drug_name="Amoxicillin",
        quantity_added=body.quantity_added,
        quantity_before=quantity_before,
        quantity_after=quantity_before + body.quantity_added,
        batch_number=body.batch_number,
        transaction_id=uuid4(),
        restocked_by=_pharmacist_name(user),
        restocked_at=_NOW,
    )


def adjust_inventory(body: AdjustInventoryRequest, user: TokenPayload) -> AdjustInventoryResponse:
    if body.inventory_id != STUB_INVENTORY_ID:
        raise NotFoundError("Inventory item not found")

    current = 179
    new_qty = current + body.quantity_change
    if new_qty < 0:
        raise ConflictError("STOCK_CANNOT_GO_NEGATIVE")

    return AdjustInventoryResponse(
        inventory_id=body.inventory_id,
        drug_name="Amoxicillin",
        quantity_in_stock=new_qty,
        transaction_id=uuid4(),
        transaction_type=body.transaction_type,
        quantity_change=body.quantity_change,
        notes=body.notes,
        adjusted_by=_pharmacist_name(user),
        adjusted_at=_NOW,
    )


def get_low_stock_alerts() -> LowStockAlertsResponse:
    return LowStockAlertsResponse(
        alert_count=1,
        alerts=[
            {
                "inventory_id": uuid4(),
                "drug_name": "Metronidazole",
                "quantity_in_stock": 12,
                "reorder_level": 50,
                "unit": "tablets",
                "shortage_gap": 38,
                "last_restocked_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
            },
        ],
    )


# ── Labels ─────────────────────────────────────────────────────────────────────

def generate_label(body: LabelGenerateRequest, user: TokenPayload) -> LabelGenerateResponse:
    if body.dispensing_id != STUB_DISPENSING_ID:
        raise NotFoundError("Dispensing record not found")

    return LabelGenerateResponse(
        label={
            "patient_name": "Jane Mwita",
            "drug_name": "Amoxicillin 500mg",
            "dose": "500mg",
            "frequency": "Three times daily",
            "duration": "7 days",
            "route": "Oral",
            "instructions": "Take after meals",
            "dispensed_date": _NOW.strftime("%d %B %Y"),
            "dispensed_by": _pharmacist_name(user),
            "batch_number": "BATCH-2025-089",
            "expiry_date": "June 2027",
        },
    )


# ── Notifications ──────────────────────────────────────────────────────────────

def list_notifications() -> PharmacyNotificationsResponse:
    return PharmacyNotificationsResponse(
        unread_count=1,
        notifications=[
            {
                "notification_id": STUB_NOTIFICATION_ID,
                "notification_type": "low_stock",
                "title": "Low Stock Alert: Metronidazole",
                "message": "Stock level (12 tablets) is at or below reorder level (50).",
                "reference_type": "drug_inventory",
                "reference_id": STUB_INVENTORY_ID,
                "created_at": _NOW,
            },
        ],
    )


def mark_notification_read(notification_id: UUID) -> MarkNotificationReadResponse:
    if notification_id != STUB_NOTIFICATION_ID:
        raise NotFoundError("Notification not found")
    return MarkNotificationReadResponse(marked_read=True)
