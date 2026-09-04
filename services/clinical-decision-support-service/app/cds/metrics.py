"""Feature-level counters for clinical decision support.

What an on-call engineer and a clinical owner need to see: how much the
capability is being used, how often it cannot produce anything, how often a
consideration is dropped for unsafe language, which rule-pack and prompt
versions are actually answering, and how often a provider fails.

**Nothing here can hold patient data.** Every counter is keyed by a member of a
fixed, closed vocabulary defined in this module. There is no path by which a
drug name, a complaint, a visit id, a patient id, an actor, or a tenant becomes
a label: `record()` refuses a key it does not already know about, so a future
caller cannot widen this into a PHI leak by passing something dynamic.

The counters are in-process and reset when the service restarts. That is the
right trade for an operational signal a scraper collects on an interval; they
are not, and must not become, the audit trail. The audit trail is the
append-only tables in the tenant database.
"""

from __future__ import annotations

import threading
from typing import Final

# The complete, closed set of counter names. record() refuses anything else.
COUNTERS: Final[frozenset[str]] = frozenset(
    {
        # Differential support
        "differential.requested",
        "differential.suggestions",
        "differential.insufficient_input",
        "differential.unavailable",
        "differential.consideration_dropped",
        "differential.red_flag_raised",
        "differential.feedback",
        # Provider health. Codes only, never a vendor message.
        "provider.not_configured",
        "provider.unavailable",
        "provider.timeout",
        "provider.invalid_output",
        # Authorization outcomes, for spotting a misconfigured role mapping.
        "authorization.denied",
        "capability.disabled_request",
    }
)

_lock = threading.Lock()
_counts: dict[str, int] = {name: 0 for name in COUNTERS}

# Latency is kept as count/total/max rather than a full histogram: enough to
# see a regression, small enough that it cannot become a per-request record.
_LATENCY_OPERATIONS: Final[frozenset[str]] = frozenset({"differential"})
_latency: dict[str, dict[str, float]] = {
    name: {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0} for name in _LATENCY_OPERATIONS
}


class UnknownCounter(KeyError):
    """Raised when a caller invents a counter name.

    Deliberately loud in tests and deliberately swallowed in production by
    record(), because a metrics mistake must never take down a clinical
    request.
    """


def record(name: str, amount: int = 1) -> None:
    """Increment one counter from the closed vocabulary above."""
    if name not in COUNTERS:
        # Never raise into a clinical path. The counter is simply not recorded,
        # and the strict variant below is what the test suite uses.
        return
    with _lock:
        _counts[name] += amount


def record_strict(name: str, amount: int = 1) -> None:
    """Increment, raising on an unknown name. For tests and static checks."""
    if name not in COUNTERS:
        raise UnknownCounter(name)
    record(name, amount)


def observe_latency(operation: str, milliseconds: float) -> None:
    """Record how long one operation took."""
    if operation not in _LATENCY_OPERATIONS:
        return
    with _lock:
        bucket = _latency[operation]
        bucket["count"] += 1
        bucket["total_ms"] += max(0.0, milliseconds)
        bucket["max_ms"] = max(bucket["max_ms"], max(0.0, milliseconds))


def snapshot() -> dict[str, object]:
    """Return the current counters, plus the versions currently answering.

    The versions matter as much as the counts: a red-flag volume that changed
    yesterday means something different depending on whether the rule pack or
    the prompt changed with it.
    """
    from app.cds.redflags import ruleset_version
    from app.core.config import settings

    with _lock:
        counters = dict(_counts)
        latency = {
            operation: {
                "count": int(values["count"]),
                "average_ms": round(values["total_ms"] / values["count"], 2)
                if values["count"]
                else 0.0,
                "max_ms": round(values["max_ms"], 2),
            }
            for operation, values in _latency.items()
        }

    return {
        "counters": counters,
        "latency": latency,
        "versions": {
            "redflag_ruleset": ruleset_version(),
            "differential_prompt": str(
                getattr(settings, "cds_differential_prompt_version", "unversioned")
            ),
        },
        "capabilities": {
            "cds_enabled": bool(getattr(settings, "cds_enabled", False)),
            "differential_support": bool(
                getattr(settings, "cds_differential_support_enabled", False)
            ),
        },
    }


def reset() -> None:
    """Zero every counter. Used by tests, never by a request path."""
    with _lock:
        for name in _counts:
            _counts[name] = 0
        for values in _latency.values():
            values["count"] = 0.0
            values["total_ms"] = 0.0
            values["max_ms"] = 0.0
