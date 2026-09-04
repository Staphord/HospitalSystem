from __future__ import annotations

from enum import Enum

from app.core.config import settings


class CdsCapability(str, Enum):
    """Independently gated clinical-decision-support capabilities."""

    DIFFERENTIAL_SUPPORT = "differential_support"


_FLAG_ATTRIBUTES: dict[CdsCapability, str] = {
    CdsCapability.DIFFERENTIAL_SUPPORT: "cds_differential_support_enabled",
}


def is_service_enabled() -> bool:
    """Return whether clinical decision support runs at all in this deployment.

    This is the whole-service kill switch. It is checked before the capability
    flag, before the role check, and before any clinical code path, so pulling
    it stops every clinical behaviour in one action while leaving ordinary
    hospital workflows in the other services untouched.
    """
    return bool(getattr(settings, "cds_enabled", False))


def is_capability_enabled(capability: CdsCapability | str) -> bool:
    """Return whether one capability is switched on.

    Fail-closed in every direction: an unknown capability name, a missing
    setting, or a disabled service resolves to off.
    """
    if not is_service_enabled():
        return False

    try:
        capability = CdsCapability(capability)
    except ValueError:
        return False

    attribute = _FLAG_ATTRIBUTES.get(capability)
    if attribute is None:
        return False
    return bool(getattr(settings, attribute, False))


def enabled_capabilities() -> list[CdsCapability]:
    """Return the capabilities currently switched on, for diagnostics."""
    return [c for c in CdsCapability if is_capability_enabled(c)]
