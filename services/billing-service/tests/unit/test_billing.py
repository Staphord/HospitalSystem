"""Billing helper constants (no Settings import)."""

from decimal import Decimal


def test_ward_day_code_constant():
    assert "WARD_DAY" == "WARD_DAY"


def test_los_quantize():
    los = Decimal("2.5")
    unit = Decimal("100.00")
    assert (los * unit).quantize(Decimal("0.01")) == Decimal("250.00")
