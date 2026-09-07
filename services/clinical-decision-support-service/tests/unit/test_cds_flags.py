"""The kill switches.

The point of these tests is that switching something off actually stops it, and
that anything unexpected resolves to off rather than on.
"""

import pytest

from app.cds.flags import (
    CdsCapability,
    enabled_capabilities,
    is_capability_enabled,
    is_service_enabled,
)
from app.core import config


@pytest.fixture
def flags(monkeypatch):
    def _set(**values):
        for key, value in values.items():
            monkeypatch.setattr(config.settings, key, value, raising=False)

    return _set


def test_everything_is_off_by_default(flags):
    flags(cds_enabled=False, cds_differential_support_enabled=False)

    assert is_service_enabled() is False
    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is False
    assert enabled_capabilities() == []


def test_the_service_switch_overrides_the_capability_switch(flags):
    """Pulling the service switch has to stop everything, not most things."""
    flags(cds_enabled=False, cds_differential_support_enabled=True)

    assert is_service_enabled() is False
    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is False
    assert enabled_capabilities() == []


def test_both_switches_on_enables_the_capability(flags):
    flags(cds_enabled=True, cds_differential_support_enabled=True)

    assert is_service_enabled() is True
    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is True
    assert enabled_capabilities() == [CdsCapability.DIFFERENTIAL_SUPPORT]


def test_an_unknown_capability_is_denied(flags):
    flags(cds_enabled=True, cds_differential_support_enabled=True)

    assert is_capability_enabled("not_a_capability") is False


def test_a_missing_setting_resolves_to_off(flags, monkeypatch):
    """A configuration gap must fail closed, not open."""
    flags(cds_enabled=True)
    monkeypatch.delattr(config.settings, "cds_differential_support_enabled", raising=False)

    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is False
