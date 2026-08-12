"""Billing business logic — ward LOS charge on discharge."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.billing import Bill, BillItem, FeeSchedule

logger = logging.getLogger("billing_service")

WARD_DAY_CODE = "WARD_DAY"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _ward_day_unit_price(db: AsyncSession) -> Decimal:
    result = await db.execute(
        select(FeeSchedule).where(
            FeeSchedule.item_code == WARD_DAY_CODE,
            FeeSchedule.is_active.is_(True),
        )
    )
    fee = result.scalars().first()
    if fee is not None:
        return Decimal(str(fee.standard_price))
    default = getattr(settings, "default_ward_day_rate", None)
    if default is None:
        default = Decimal("100.00")
    else:
        default = Decimal(str(default))
    logger.warning(
        "No fee_schedules row for %s; using DEFAULT_WARD_DAY_RATE=%s",
        WARD_DAY_CODE,
        default,
    )
    return default


async def apply_ward_charge_on_discharge(
    db: AsyncSession,
    *,
    admission_id: str,
    patient_id: str,
    tenant_id: str,
    length_of_stay_days: float,
    visit_id: str | None = None,
) -> BillItem | None:
    """Idempotent: one WARD_DAY line per admission (source_ref=admission_id)."""
    los = Decimal(str(max(length_of_stay_days, 0.0)))
    if los <= 0:
        los = Decimal("0.1")

    adm_uuid = uuid.UUID(admission_id)
    patient_uuid = uuid.UUID(patient_id)
    visit_uuid = uuid.UUID(visit_id) if visit_id else None

    bill_result = await db.execute(select(Bill).where(Bill.admission_id == adm_uuid))
    bill = bill_result.scalars().first()
    if not bill:
        bill = Bill(
            bill_id=uuid.uuid4(),
            visit_id=visit_uuid,
            admission_id=adm_uuid,
            patient_id=patient_uuid,
            status="open",
            total_amount=Decimal("0"),
        )
        db.add(bill)
        await db.flush()

    existing = await db.execute(
        select(BillItem).where(
            BillItem.bill_id == bill.bill_id,
            BillItem.item_code == WARD_DAY_CODE,
            BillItem.source_ref == admission_id,
        )
    )
    if existing.scalars().first():
        logger.info("Ward charge already present for admission %s — skipping", admission_id)
        return None

    unit = await _ward_day_unit_price(db)
    line_total = (los * unit).quantize(Decimal("0.01"))
    item = BillItem(
        item_id=uuid.uuid4(),
        bill_id=bill.bill_id,
        item_code=WARD_DAY_CODE,
        item_type="ward",
        description=f"Ward stay ({los} day(s))",
        quantity=los,
        unit_price=unit,
        line_total=line_total,
        source_ref=admission_id,
    )
    db.add(item)
    bill.total_amount = Decimal(str(bill.total_amount or 0)) + line_total
    bill.updated_at = _utcnow()
    await db.commit()
    await db.refresh(item)
    logger.info(
        "Created ward charge admission=%s qty=%s unit=%s total=%s tenant=%s",
        admission_id,
        los,
        unit,
        line_total,
        tenant_id,
    )
    return item


async def apply_drug_charge(
    db: AsyncSession,
    *,
    dispensing_id: str,
    tenant_id: str,
) -> BillItem | None:
    """Query dispensing_records and add drug line total to visit bill."""
    disp_uuid = uuid.UUID(dispensing_id)

    res = await db.execute(
        text("""
            SELECT d.visit_id, d.quantity_dispensed, i.drug_name, i.drug_code, i.unit_price, p.drug_name AS presc_drug_name
            FROM dispensing_records d
            LEFT JOIN drug_inventory i ON d.inventory_id = i.inventory_id
            LEFT JOIN pharmacy_prescription_items p ON d.prescription_item_id = p.prescription_item_id
            WHERE d.dispensing_id = :dispensing_id
        """),
        {"dispensing_id": disp_uuid},
    )
    row = res.fetchone()
    if not row:
        logger.error("Dispensing record %s not found in tenant database", dispensing_id)
        return None

    visit_id = row[0]
    quantity = Decimal(str(row[1] or 1))
    drug_name = row[2] or row[5] or "Dispensed Medication"
    drug_code = row[3] or "DRUG_GENERIC"
    unit_price = Decimal(str(row[4] or 10.00))

    if not visit_id:
        logger.error("Dispensing record %s lacks visit_id", dispensing_id)
        return None

    visit_res = await db.execute(
        text("SELECT patient_id FROM visits WHERE visit_id = :visit_id"),
        {"visit_id": visit_id},
    )
    visit_row = visit_res.fetchone()
    if not visit_row:
        logger.error("Visit %s not found for dispensing %s", visit_id, dispensing_id)
        return None
    patient_id = visit_row[0]

    bill_result = await db.execute(select(Bill).where(Bill.visit_id == visit_id))
    bill = bill_result.scalars().first()
    if not bill:
        bill = Bill(
            bill_id=uuid.uuid4(),
            visit_id=visit_id,
            patient_id=patient_id,
            status="open",
            total_amount=Decimal("0.00"),
        )
        db.add(bill)
        await db.flush()

    existing = await db.execute(
        select(BillItem).where(
            BillItem.bill_id == bill.bill_id,
            BillItem.source_ref == dispensing_id,
        )
    )
    if existing.scalars().first():
        logger.info("Drug charge already present for dispensing %s — skipping", dispensing_id)
        return None

    line_total = (quantity * unit_price).quantize(Decimal("0.01"))
    item = BillItem(
        item_id=uuid.uuid4(),
        bill_id=bill.bill_id,
        item_code=drug_code,
        item_type="pharmacy",
        description=f"Dispensed: {drug_name} x {quantity}",
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
        source_ref=dispensing_id,
    )
    db.add(item)
    bill.total_amount = Decimal(str(bill.total_amount or 0)) + line_total
    bill.updated_at = _utcnow()
    await db.commit()
    await db.refresh(item)
    logger.info(
        "Created drug charge dispensing=%s name=%s qty=%s price=%s total=%s tenant=%s",
        dispensing_id,
        drug_name,
        quantity,
        unit_price,
        line_total,
        tenant_id,
    )
    return item
