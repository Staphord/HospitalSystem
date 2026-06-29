"""Pharmacy inventory — database-backed stock management."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AdjustInventoryRequest,
    AdjustInventoryResponse,
    InventoryDetailResponse,
    InventoryListItem,
    InventoryListResponse,
    InventoryTransactionItem,
    LowStockAlertItem,
    LowStockAlertsResponse,
    RestockRequest,
    RestockResponse,
)
from app.core.security import TokenPayload
from app.exceptions import ConflictError, NotFoundError
from app.models.pharmacy import DrugInventory, DrugInventoryTransaction

# Stable seed UUIDs (match migration 0007)
SEED_INVENTORY_AMOXICILLIN_ID = UUID("e5000005-0005-4005-8005-000000000005")
SEED_INVENTORY_METRONIDAZOLE_ID = UUID("e5000005-0005-4005-8005-000000000099")


def _pharmacist_name(user: TokenPayload) -> str:
    return user.preferred_username or user.email or "Pharmacist"


def _is_low_stock(item: DrugInventory) -> bool:
    return item.quantity_in_stock <= item.reorder_level


def _to_list_item(item: DrugInventory) -> InventoryListItem:
    return InventoryListItem(
        inventory_id=item.inventory_id,
        drug_name=item.drug_name,
        brand_name=item.brand_name,
        drug_code=item.drug_code,
        category=item.category,
        unit=item.unit,
        quantity_in_stock=item.quantity_in_stock,
        reorder_level=item.reorder_level,
        is_low_stock=_is_low_stock(item),
        unit_cost=float(item.unit_cost),
        unit_price=float(item.unit_price),
        location=item.location,
        last_restocked_at=item.last_restocked_at,
    )


def _to_transaction_item(tx: DrugInventoryTransaction) -> InventoryTransactionItem:
    return InventoryTransactionItem(
        transaction_id=tx.transaction_id,
        transaction_type=tx.transaction_type,
        quantity_change=tx.quantity_change,
        quantity_before=tx.quantity_before,
        quantity_after=tx.quantity_after,
        performed_by=tx.performed_by_name or tx.performed_by,
        created_at=tx.created_at,
    )


async def _get_inventory_or_404(db: AsyncSession, inventory_id: UUID) -> DrugInventory:
    result = await db.execute(
        select(DrugInventory).where(
            DrugInventory.inventory_id == inventory_id,
            DrugInventory.is_active.is_(True),
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundError("Inventory item not found")
    return item


async def list_inventory(
    db: AsyncSession,
    search: str | None,
    category: str | None,
    low_stock: bool | None,
    page: int,
    page_size: int,
) -> InventoryListResponse:
    filters = [DrugInventory.is_active.is_(True)]

    if search:
        needle = f"%{search.strip()}%"
        filters.append(
            or_(
                DrugInventory.drug_name.ilike(needle),
                DrugInventory.brand_name.ilike(needle),
                DrugInventory.drug_code.ilike(needle),
            )
        )
    if category:
        filters.append(DrugInventory.category.ilike(category.strip()))
    if low_stock is True:
        filters.append(DrugInventory.quantity_in_stock <= DrugInventory.reorder_level)
    elif low_stock is False:
        filters.append(DrugInventory.quantity_in_stock > DrugInventory.reorder_level)

    count_result = await db.execute(
        select(func.count(DrugInventory.inventory_id)).where(*filters)
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(
        select(DrugInventory)
        .where(*filters)
        .order_by(DrugInventory.drug_name.asc())
        .offset(offset)
        .limit(page_size)
    )
    items = [_to_list_item(row) for row in result.scalars().all()]

    return InventoryListResponse(total=total, page=page, page_size=page_size, items=items)


async def get_inventory_detail(
    db: AsyncSession,
    inventory_id: UUID,
) -> InventoryDetailResponse:
    item = await _get_inventory_or_404(db, inventory_id)

    tx_result = await db.execute(
        select(DrugInventoryTransaction)
        .where(DrugInventoryTransaction.inventory_id == inventory_id)
        .order_by(DrugInventoryTransaction.created_at.desc())
        .limit(20)
    )
    transactions = [_to_transaction_item(tx) for tx in tx_result.scalars().all()]

    return InventoryDetailResponse(
        **_to_list_item(item).model_dump(),
        recent_transactions=transactions,
    )


async def restock_inventory(
    db: AsyncSession,
    body: RestockRequest,
    user: TokenPayload,
) -> RestockResponse:
    item = await _get_inventory_or_404(db, body.inventory_id)
    now = datetime.now(timezone.utc)
    quantity_before = item.quantity_in_stock
    quantity_after = quantity_before + body.quantity_added

    item.quantity_in_stock = quantity_after
    item.unit_cost = Decimal(str(body.unit_cost))
    item.last_restocked_at = now
    item.updated_at = now

    tx = DrugInventoryTransaction(
        inventory_id=item.inventory_id,
        transaction_type="restock",
        quantity_change=body.quantity_added,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        unit_cost=Decimal(str(body.unit_cost)),
        notes=body.notes,
        performed_by=user.sub or "unknown",
        performed_by_name=_pharmacist_name(user),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(item)
    await db.refresh(tx)

    return RestockResponse(
        inventory_id=item.inventory_id,
        drug_name=item.drug_name,
        quantity_added=body.quantity_added,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        batch_number=body.batch_number,
        transaction_id=tx.transaction_id,
        restocked_by=_pharmacist_name(user),
        restocked_at=now,
    )


async def adjust_inventory(
    db: AsyncSession,
    body: AdjustInventoryRequest,
    user: TokenPayload,
) -> AdjustInventoryResponse:
    item = await _get_inventory_or_404(db, body.inventory_id)
    now = datetime.now(timezone.utc)
    quantity_before = item.quantity_in_stock
    quantity_after = quantity_before + body.quantity_change

    if quantity_after < 0:
        raise ConflictError("STOCK_CANNOT_GO_NEGATIVE")

    item.quantity_in_stock = quantity_after
    item.updated_at = now

    tx = DrugInventoryTransaction(
        inventory_id=item.inventory_id,
        transaction_type=body.transaction_type,
        quantity_change=body.quantity_change,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        notes=body.notes,
        performed_by=user.sub or "unknown",
        performed_by_name=_pharmacist_name(user),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(item)
    await db.refresh(tx)

    return AdjustInventoryResponse(
        inventory_id=item.inventory_id,
        drug_name=item.drug_name,
        quantity_in_stock=quantity_after,
        transaction_id=tx.transaction_id,
        transaction_type=body.transaction_type,
        quantity_change=body.quantity_change,
        notes=body.notes,
        adjusted_by=_pharmacist_name(user),
        adjusted_at=now,
    )


async def get_low_stock_alerts(db: AsyncSession) -> LowStockAlertsResponse:
    result = await db.execute(
        select(DrugInventory)
        .where(
            DrugInventory.is_active.is_(True),
            DrugInventory.quantity_in_stock <= DrugInventory.reorder_level,
        )
        .order_by(DrugInventory.quantity_in_stock.asc())
    )
    rows = result.scalars().all()
    alerts = [
        LowStockAlertItem(
            inventory_id=row.inventory_id,
            drug_name=row.drug_name,
            quantity_in_stock=row.quantity_in_stock,
            reorder_level=row.reorder_level,
            unit=row.unit,
            shortage_gap=max(row.reorder_level - row.quantity_in_stock, 0),
            last_restocked_at=row.last_restocked_at,
        )
        for row in rows
    ]
    return LowStockAlertsResponse(alert_count=len(alerts), alerts=alerts)
