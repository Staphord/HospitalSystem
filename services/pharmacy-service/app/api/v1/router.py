from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AdjustInventoryRequest,
    AdjustInventoryResponse,
    DispenseRequest,
    DispenseResponse,
    DispenseSummaryResponse,
    InteractionCheckResponse,
    InventoryDetailResponse,
    InventoryListResponse,
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
from app.core.security import TokenPayload, require_role
from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db
from app.services import inventory as inventory_service
from app.services import pharmacy as pharmacy_service

router = APIRouter(
    dependencies=[
        Depends(get_current_tenant),
        Depends(require_role("pharmacist")),
    ],
)


# ── Queue ──────────────────────────────────────────────────────────────────────

@router.get("/queue", response_model=PharmacyQueueResponse, tags=["Queue"])
async def get_pharmacy_queue(
    status: Literal["waiting", "in_progress", "completed"] = Query("waiting"),
    queue_date: date = Query(default_factory=date.today, alias="date"),
) -> PharmacyQueueResponse:
    return pharmacy_service.get_pharmacy_queue(queue_date, status)


@router.patch("/queue/{queue_id}/call", response_model=PharmacyQueueItem, tags=["Queue"])
async def call_queue_patient(
    queue_id: UUID,
    user: TokenPayload = Depends(require_role("pharmacist")),
) -> PharmacyQueueItem:
    return pharmacy_service.call_queue_patient(queue_id, user)


# ── Prescriptions ──────────────────────────────────────────────────────────────

@router.get("/prescriptions/{visit_id}", response_model=VisitPrescriptionsResponse, tags=["Prescriptions"])
async def get_visit_prescriptions(
    visit_id: UUID,
) -> VisitPrescriptionsResponse:
    return pharmacy_service.get_visit_prescriptions(visit_id)


@router.get(
    "/prescriptions/{visit_id}/interaction-check",
    response_model=InteractionCheckResponse,
    tags=["Prescriptions"],
)
async def check_drug_interactions(
    visit_id: UUID,
) -> InteractionCheckResponse:
    return pharmacy_service.check_drug_interactions(visit_id)


# ── Dispensing ─────────────────────────────────────────────────────────────────

@router.post("/dispense", response_model=DispenseResponse, status_code=201, tags=["Dispensing"])
async def dispense_prescription(
    body: DispenseRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
) -> DispenseResponse:
    return pharmacy_service.dispense_prescription(body, user)


@router.get("/dispense/{visit_id}/summary", response_model=DispenseSummaryResponse, tags=["Dispensing"])
async def get_dispense_summary(
    visit_id: UUID,
) -> DispenseSummaryResponse:
    return pharmacy_service.get_dispense_summary(visit_id)


# ── Inventory (static paths before /{inventory_id}) ────────────────────────────

@router.get("/inventory/low-stock-alerts", response_model=LowStockAlertsResponse, tags=["Inventory"])
async def get_low_stock_alerts(
    db: AsyncSession = Depends(get_tenant_db),
) -> LowStockAlertsResponse:
    return await inventory_service.get_low_stock_alerts(db)


@router.post("/inventory/restock", response_model=RestockResponse, status_code=201, tags=["Inventory"])
async def restock_inventory(
    body: RestockRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> RestockResponse:
    return await inventory_service.restock_inventory(db, body, user)


@router.post("/inventory/adjust", response_model=AdjustInventoryResponse, status_code=201, tags=["Inventory"])
async def adjust_inventory(
    body: AdjustInventoryRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> AdjustInventoryResponse:
    return await inventory_service.adjust_inventory(db, body, user)


@router.get("/inventory", response_model=InventoryListResponse, tags=["Inventory"])
async def list_inventory(
    search: str | None = Query(None),
    category: str | None = Query(None),
    low_stock: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> InventoryListResponse:
    return await inventory_service.list_inventory(db, search, category, low_stock, page, page_size)


@router.get("/inventory/{inventory_id}", response_model=InventoryDetailResponse, tags=["Inventory"])
async def get_inventory_detail(
    inventory_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> InventoryDetailResponse:
    return await inventory_service.get_inventory_detail(db, inventory_id)


# ── Labels ─────────────────────────────────────────────────────────────────────

@router.post("/labels/generate", response_model=LabelGenerateResponse, tags=["Labels"])
async def generate_label(
    body: LabelGenerateRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
) -> LabelGenerateResponse:
    return pharmacy_service.generate_label(body, user)


# ── Notifications ──────────────────────────────────────────────────────────────

@router.get("/notifications", response_model=PharmacyNotificationsResponse, tags=["Notifications"])
async def list_notifications(
) -> PharmacyNotificationsResponse:
    return pharmacy_service.list_notifications()


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=MarkNotificationReadResponse,
    tags=["Notifications"],
)
async def mark_notification_read(
    notification_id: UUID,
) -> MarkNotificationReadResponse:
    return pharmacy_service.mark_notification_read(notification_id)
