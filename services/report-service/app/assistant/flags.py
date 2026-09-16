from __future__ import annotations

from enum import Enum

from app.core.config import settings


class AssistantCapability(str, Enum):
    """The parts of the assistant, named.

    These are no longer separately switchable. They remain distinct because the
    role matrix in permissions.py, the audit records, and the capability list
    the assistant reads back to a caller all need to say *which* part of the
    assistant was involved - "a doctor was refused the medicines reference" is
    a different fact from "a doctor was refused the chat".
    """

    OPERATIONAL_CHAT = "operational_chat"
    VOICE = "voice"
    MEDICATION_CHECK = "medication_check"
    DIFFERENTIAL_SUPPORT = "differential_support"
    REALTIME_VOICE = "realtime_voice"
    LIVE_DATA = "live_data"
    CHAT_HISTORY = "chat_history"


def is_assistant_enabled() -> bool:
    """Whether the assistant exists for this deployment at all.

    One switch, ASSISTANT_OPERATIONAL_CHAT_ENABLED, and it is the only
    assistant setting still read from the environment beyond the medicines
    model fallback. Off is the default and is absolute: every assistant route
    answers 404, the status route tells the browser not to render the launcher,
    and no provider call is reachable from anywhere in this service.
    """
    return bool(getattr(settings, "assistant_operational_chat_enabled", False))


def is_capability_enabled(capability: AssistantCapability | str) -> bool:
    """Whether a named capability is available for this deployment.

    Every capability now answers the one switch. The per-capability flags this
    used to read were there to stage a phased rollout: each phase shipped
    disabled and was turned on once it passed its exit gate. Those gates have
    passed, and keeping the flags meant a deployment could leave the assistant
    half-enabled - a launcher whose microphone button answered 404, a history
    panel that silently stored nothing - which is a worse failure than not
    having the feature, because staff cannot tell it from a bug.

    What a given person may actually reach is a separate question, answered by
    role in permissions.py and unchanged by this: medicines still reaches only
    doctors and pharmacists, and a platform super admin still reaches none of
    it.

    Fail-closed on an unrecognized capability, so a typo disables rather than
    enables.
    """
    try:
        AssistantCapability(capability)
    except ValueError:
        return False

    return is_assistant_enabled()


def enabled_capabilities() -> list[AssistantCapability]:
    """The capabilities currently available, for diagnostics.

    All of them, or none of them.
    """
    if not is_assistant_enabled():
        return []
    return list(AssistantCapability)
