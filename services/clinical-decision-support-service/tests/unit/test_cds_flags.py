"""The kill switches.

The point of these tests is that switching something off actually stops it, and
that anything unexpected resolves to off rather than on.
"""

import pytest

from app.cds.flags import CdsCapability, enabled_capabilities, is_capability_enabled, is_service_enabled
from app.core import config


@pytest.fixture
def flags(monkeypatch):
    def _set(**values):
        for key, value in values.items():
            monkeypatch.setattr(config.settings, key, value, raising=False)

    return _set


def test_everything_is_off_by_default(flags):
    flags(cds_enabled=False, cds_medication_check_enabled=False, cds_differential_support_enabled=False)

    assert is_service_enabled() is False
    assert is_capability_enabled(CdsCapability.MEDICATION_CHECK) is False
    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is False
    assert enabled_capabilities() == []


def test_service_switch_overrides_an_enabled_capability(flags):
    flags(cds_enabled=False, cds_medication_check_enabled=True)

    # Pulling the service switch has to stop the capability even when the
    # capability's own flag was left on, or the whole-service kill switch is a
    # lie during an incident.
    assert is_capability_enabled(CdsCapability.MEDICATION_CHECK) is False


def test_capabilities_are_independent(flags):
    flags(
        cds_enabled=True,
        cds_medication_check_enabled=True,
        cds_differential_support_enabled=False,
    )

    assert is_capability_enabled(CdsCapability.MEDICATION_CHECK) is True
    assert is_capability_enabled(CdsCapability.DIFFERENTIAL_SUPPORT) is False
    assert enabled_capabilities() == [CdsCapability.MEDICATION_CHECK]


def test_unknown_capability_is_disabled(flags):
    flags(cds_enabled=True, cds_medication_check_enabled=True)

    assert is_capability_enabled("not_a_capability") is False
    assert is_capability_enabled("") is False
    assert is_capability_enabled(None) is False


def test_missing_setting_is_disabled(monkeypatch):
    monkeypatch.setattr(config.settings, "cds_enabled", True, raising=False)
    monkeypatch.delattr(config.settings, "cds_medication_check_enabled", raising=False)

    assert is_capability_enabled(CdsCapability.MEDICATION_CHECK) is False
