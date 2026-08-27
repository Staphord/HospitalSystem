from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

# Logging controls for the assistant.
#
# An assistant log line answers "who asked what kind of question, against which
# versions, with what outcome". It never answers "what did they ask" or "what
# were they told". Question text, answer text, transcripts, retrieved content,
# provider payloads, credentials, and stack traces are not loggable values here.

REDACTED = "[redacted]"

# Field names that may appear in an assistant log line. Deny by default: a name
# that is not on this list is dropped rather than logged.
LOGGABLE_FIELDS: frozenset[str] = frozenset(
    {
        "request_id",
        "actor_sub",
        "tenant_id",
        "capability",
        "outcome",
        "provider",
        "model_version",
        "content_pack_version",
        "duration_ms",
        "status",
        "error_code",
        "tool",
        "item_count",
        # Voice. Non-content metadata about a capture only: how big it was, how
        # long, what container and codec, which language was detected, and how
        # many characters the transcript ran to. The transcript itself, and any
        # audio byte, remain unloggable.
        "audio_container",
        "audio_codec",
        "audio_bytes_size",
        "audio_duration_ms",
        "duration_source",
        "language",
        "rejection",
        "transcript_chars",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)postgres(?:ql)?(?:\+\w+)?://[^\s\"']+"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[=:]\s*\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
)


def scrub(value: Any) -> str:
    """Remove anything credential-shaped from a string before it is logged."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def safe_log_fields(**fields: Any) -> dict[str, Any]:
    """Keep only allowlisted, scrubbed fields.

    Values that are not simple scalars are dropped rather than serialised, so a
    dict of retrieved content or a provider response object can never be logged
    by accident.
    """
    safe: dict[str, Any] = {}
    for name, value in fields.items():
        if name not in LOGGABLE_FIELDS or value is None:
            continue
        # Enums are matched before str and int, because the enums used here
        # subclass str: without this order they would fall into the str branch
        # and log as "AssistantOutcome.SUCCESS" rather than as "success", which
        # is the repr this function exists to avoid.
        if isinstance(value, Enum) and isinstance(value.value, (str, int, float)):
            safe[name] = scrub(value.value)
        elif isinstance(value, bool) or isinstance(value, int):
            safe[name] = value
        elif isinstance(value, (str, float)):
            safe[name] = scrub(value)
        # Anything else (a dict of retrieved content, a provider response, a
        # model object) is dropped rather than serialised. Falling back to str()
        # here is how a payload would end up in a log line.
    return safe


def log_assistant_event(
    logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any
) -> None:
    """Log one assistant event using allowlisted fields only."""
    safe = safe_log_fields(**fields)
    rendered = " ".join(f"{k}={v}" for k, v in sorted(safe.items()))
    logger.log(level, "%s %s", scrub(message), rendered)
