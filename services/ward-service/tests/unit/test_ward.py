"""Unit tests for ward LOS and role helpers."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.ward import ORDER_TYPES, NOTE_TYPES, compute_los_days


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
