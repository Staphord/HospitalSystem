from __future__ import annotations

from enum import Enum

from app.core.config import settings


class AssistantCapability(str, Enum):
    """Independently gated assistant capabilities."""

    OPERATIONAL_CHAT = "operational_chat"
    VOICE = "voice"
    MEDICATION_CHECK = "medication_check"
    DIFFERENTIAL_SUPPORT = "differential_support"
    REALTIME_VOICE = "realtime_voice"
    LIVE_DATA = "live_data"
    CHAT_HISTORY = "chat_history"


_FLAG_ATTRIBUTES: dict[AssistantCapability, str] = {
    AssistantCapability.OPERATIONAL_CHAT: "assistant_operational_chat_enabled",
    AssistantCapability.VOICE: "assistant_voice_enabled",
    AssistantCapability.MEDICATION_CHECK: "assistant_medication_check_enabled",
    AssistantCapability.DIFFERENTIAL_SUPPORT: "assistant_differential_support_enabled",
    AssistantCapability.REALTIME_VOICE: "assistant_realtime_voice_enabled",
    AssistantCapability.CHAT_HISTORY: "assistant_chat_history_enabled",
    AssistantCapability.LIVE_DATA: "assistant_live_data_enabled",
}


def is_capability_enabled(capability: AssistantCapability | str) -> bool:
    """Return whether a capability is switched on for this deployment.

    Fail-closed: an unknown capability, a missing setting, or any lookup error
    resolves to disabled. Operator flags are the kill switch and are evaluated
    before any subscription-module or role check.
    """
    try:
        capability = AssistantCapability(capability)
    except ValueError:
        return False

    attribute = _FLAG_ATTRIBUTES.get(capability)
    if attribute is None:
        return False
    return bool(getattr(settings, attribute, False))


def enabled_capabilities() -> list[AssistantCapability]:
    """Return the capabilities currently switched on, for diagnostics."""
    return [c for c in AssistantCapability if is_capability_enabled(c)]
