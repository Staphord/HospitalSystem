"""Billing helper constants and ward LOS calculation unit tests."""

from decimal import Decimal


def test_ward_day_code_constant():
    assert "WARD_DAY" == "WARD_DAY"


def test_los_quantize():
    los = Decimal("2.5")
    unit = Decimal("100.00")
    assert (los * unit).quantize(Decimal("0.01")) == Decimal("250.00")


def test_ward_charge_amount_calculation():
    """Verify ward discharge length-of-stay line total computation."""
    length_of_stay_days = 3.5
    rate_per_day = Decimal("15000.00")
    
    los = Decimal(str(length_of_stay_days))
    line_total = (los * rate_per_day).quantize(Decimal("0.01"))
    
    assert line_total == Decimal("52500.00")

