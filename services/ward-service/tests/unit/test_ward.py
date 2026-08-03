"""Unit tests for ward LOS, role, and condition helpers."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services.ward import (
    ADMISSION_CONDITIONS,
    NOTE_TYPES,
    ORDER_TYPES,
    compute_los_days,
    update_admission_condition,
)


def test_compute_los_days_same_instant():
    now = datetime.now(timezone.utc)
    assert compute_los_days(now, now) == Decimal("0.0")


def test_compute_los_days_one_day():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    assert compute_los_days(start, end) == Decimal("1.0")


def test_order_and_note_type_sets():
    assert "medication" in ORDER_TYPES
    assert "diet" in ORDER_TYPES
    assert "observation" in NOTE_TYPES
    assert "ward_round" in NOTE_TYPES


def test_admission_condition_set():
    assert ADMISSION_CONDITIONS == {"stable", "monitoring", "critical"}


def test_update_admission_condition_rejects_invalid_value():
    # Validation happens before any DB access, so a bogus session is fine here.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            update_admission_condition(
                db=None,
                admission_id="00000000-0000-0000-0000-000000000000",
                condition="dying",
                actor_sub="nurse-1",
            )
        )
    assert exc_info.value.status_code == 400
