from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AdjustInventoryRequest,
    AdjustInventoryResponse,
    CreateInventoryRequest,
    DispenseRequest,
    DispenseResponse,
    DispenseSummaryResponse,
    InteractionCheckResponse,
    InventoryDetailResponse,
    InventoryListItem,
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
    UpdateInventoryRequest,
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
    queue_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_tenant_db),
) -> PharmacyQueueResponse:
    return await pharmacy_service.get_pharmacy_queue(db, queue_date, status)



@router.patch("/queue/{queue_id}/call", response_model=PharmacyQueueItem, tags=["Queue"])
async def call_queue_patient(
    queue_id: UUID,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> PharmacyQueueItem:
    return await pharmacy_service.call_queue_patient(db, queue_id, user)


# ── Prescriptions ──────────────────────────────────────────────────────────────

@router.get("/prescriptions/{visit_id}", response_model=VisitPrescriptionsResponse, tags=["Prescriptions"])
async def get_visit_prescriptions(
    visit_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> VisitPrescriptionsResponse:
    return await pharmacy_service.get_visit_prescriptions(db, visit_id)


@router.get(
    "/prescriptions/{visit_id}/interaction-check",
    response_model=InteractionCheckResponse,
    tags=["Prescriptions"],
)
async def check_drug_interactions(
    visit_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> InteractionCheckResponse:
    return await pharmacy_service.check_drug_interactions(db, visit_id)


# ── Dispensing ─────────────────────────────────────────────────────────────────

@router.post("/dispense", response_model=DispenseResponse, status_code=201, tags=["Dispensing"])
async def dispense_prescription(
    body: DispenseRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> DispenseResponse:
    return await pharmacy_service.dispense_prescription(db, body, user)


@router.get("/dispense/{visit_id}/summary", response_model=DispenseSummaryResponse, tags=["Dispensing"])
async def get_dispense_summary(
    visit_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> DispenseSummaryResponse:
    return await pharmacy_service.get_dispense_summary(db, visit_id)


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


@router.post("/inventory", response_model=InventoryListItem, status_code=201, tags=["Inventory"])
async def create_inventory_item(
    body: CreateInventoryRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> InventoryListItem:
    return await inventory_service.create_inventory_item(db, body, user)


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


@router.patch("/inventory/{inventory_id}", response_model=InventoryListItem, tags=["Inventory"])
async def update_inventory_item(
    inventory_id: UUID,
    body: UpdateInventoryRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> InventoryListItem:
    return await inventory_service.update_inventory_item(db, inventory_id, body, user)


# ── Labels ─────────────────────────────────────────────────────────────────────

@router.post("/labels/generate", response_model=LabelGenerateResponse, tags=["Labels"])
async def generate_label(
    body: LabelGenerateRequest,
    user: TokenPayload = Depends(require_role("pharmacist")),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabelGenerateResponse:
    return await pharmacy_service.generate_label(db, body, user)


# ── Notifications ──────────────────────────────────────────────────────────────

@router.get("/notifications", response_model=PharmacyNotificationsResponse, tags=["Notifications"])
async def list_notifications(
    db: AsyncSession = Depends(get_tenant_db),
) -> PharmacyNotificationsResponse:
    return await pharmacy_service.list_notifications(db)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=MarkNotificationReadResponse,
    tags=["Notifications"],
)
async def mark_notification_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> MarkNotificationReadResponse:
    return await pharmacy_service.mark_notification_read(db, notification_id)
