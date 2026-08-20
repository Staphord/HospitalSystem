"""Billing helper and idempotent charge unit tests."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import billing


def test_ward_day_code_constant():
    assert billing.WARD_DAY_CODE == "WARD_DAY"


@pytest.mark.asyncio
async def test_ward_day_unit_price_uses_default_when_fee_missing():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    db.execute.return_value = result

    assert await billing._ward_day_unit_price(db) == Decimal("100.00")


@pytest.mark.asyncio
async def test_ward_day_unit_price_uses_active_fee_schedule():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = MagicMock(standard_price=Decimal("275.50"))
    db.execute.return_value = result

    assert await billing._ward_day_unit_price(db) == Decimal("275.50")


@pytest.mark.asyncio
async def test_ward_charge_normalizes_non_positive_length_of_stay():
    db = AsyncMock()
    db.add = MagicMock()
    bill = MagicMock(bill_id=uuid4(), total_amount=Decimal("0"))
    bill_result = MagicMock()
    bill_result.scalars.return_value.first.return_value = bill
    no_existing_item = MagicMock()
    no_existing_item.scalars.return_value.first.return_value = None
    fee_result = MagicMock()
    fee_result.scalars.return_value.first.return_value = MagicMock(standard_price=Decimal("100"))
    db.execute.side_effect = [bill_result, no_existing_item, fee_result]

    item = await billing.apply_ward_charge_on_discharge(
        db,
        admission_id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id="tenant-1",
        length_of_stay_days=0,
    )

    assert item.quantity == Decimal("0.1")
    assert item.line_total == Decimal("10.00")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_drug_charge_returns_none_when_dispensing_record_missing():
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = None
    db.execute.return_value = result

    assert await billing.apply_drug_charge(db, dispensing_id=str(uuid4()), tenant_id="tenant-1") is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_rejects_malformed_identifiers_before_database_access():
    db = AsyncMock()

    with pytest.raises(ValueError):
        await billing.apply_ward_charge_on_discharge(
            db,
            admission_id="not-a-uuid",
            patient_id=str(uuid4()),
            tenant_id="tenant-1",
            length_of_stay_days=1,
        )

    with pytest.raises(ValueError):
        await billing.apply_drug_charge(db, dispensing_id="not-a-uuid", tenant_id="tenant-1")

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ward_charge_is_idempotent_for_same_admission():
    db = AsyncMock()
    bill_result = MagicMock()
    bill_result.scalars.return_value.first.return_value = MagicMock(bill_id=uuid4())
    existing_result = MagicMock()
    existing_result.scalars.return_value.first.return_value = MagicMock()
    db.execute.side_effect = [bill_result, existing_result]

    result = await billing.apply_ward_charge_on_discharge(
        db,
        admission_id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id="tenant-1",
        length_of_stay_days=3.5,
    )

    assert result is None
    db.commit.assert_not_awaited()
