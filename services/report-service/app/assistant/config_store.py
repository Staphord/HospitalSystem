"""Runtime assistant configuration, owned by the platform super admin.

Everything the assistant needs at request time that is not a capability switch
lives here: which provider and model answer a question, how long it may take,
how much a caller may send, and how much history is kept. These used to be
environment variables, which meant changing a model or rotating a key required
a deploy and a restart of every replica. They are now one row in the master
database, edited from the super admin portal.

Three properties this module is responsible for:

* **Fail-safe reads.** A missing table, an unreachable database, or an empty
  row all resolve to the built-in defaults rather than raising. The assistant
  answering questions must not stop because a configuration row is missing.
* **Bounded values.** Every number is clamped to a range that keeps the service
  correct - most importantly the timeouts, which must stay below the API
  gateway's fixed 30 second proxy timeout, or a slow answer reaches the browser
  as a gateway error instead of the assistant's own refusal.
* **Secret containment.** The API key is encrypted at rest with the same Fernet
  key that protects tenant connection strings, decrypted only here, and never
  returned to a browser, written to a log, or placed in an audit record.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import inspect as sa_inspect

from app.core.config import settings
from app.db.master import get_master_session
from app.models.master import AssistantConfig
from app.services.tenant_service import decrypt_dsn, encrypt_dsn

logger = logging.getLogger("assistant.config")

# The single row. There is one assistant for the platform, not one per tenant.
CONFIG_ROW_ID = 1

# How long a loaded configuration is reused before the row is read again. Short
# enough that a super admin sees their change take effect while they are still
# looking at the page, long enough that a busy chat does not read the row on
# every message.
CACHE_TTL_SECONDS = 10.0


# -- Selectable models -------------------------------------------------------
#
# An allowlist, not a free text field. Two reasons it is enforced server-side:
#
# 1. The agentic groq/compound models perform server-side web search and code
#    execution. The assistant must never reach either, so they are not offered
#    and are refused if submitted.
# 2. A typo in a model id fails at the provider with an opaque error, minutes
#    after the save, in whatever ward happened to ask the next question.
#
# Keep this list in step with the Groq catalogue. A retired id must be removed
# here rather than left to fail at runtime, which is what happened to
# llama-3.3-70b-versatile.

CHAT_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "note": "Default. Strongest reasoning of the approved set.",
    },
    {
        "id": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B",
        "note": "Faster and cheaper. Weaker on multi-step questions.",
    },
    {
        "id": "llama-3.1-8b-instant",
        "label": "Llama 3.1 8B Instant",
        "note": "Lowest latency. Short operational lookups only.",
    },
    {
        "id": "meta-llama/llama-4-scout-17b-16e-instruct",
        "label": "Llama 4 Scout 17B",
        "note": "Balanced speed and quality.",
    },
    {
        "id": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "label": "Llama 4 Maverick 17B",
        "note": "Longer context than Scout, at higher latency.",
    },
    {
        "id": "qwen/qwen3-32b",
        "label": "Qwen 3 32B",
        "note": "Strong multilingual handling, including Swahili.",
    },
)

TRANSCRIPTION_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "whisper-large-v3",
        "label": "Whisper Large v3",
        "note": "Default. Required for Swahili and code-mixed speech.",
    },
    {
        "id": "whisper-large-v3-turbo",
        "label": "Whisper Large v3 Turbo",
        "note": "Faster, but trades multilingual accuracy for speed.",
    },
)

CHAT_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in CHAT_MODELS)
TRANSCRIPTION_MODEL_IDS: frozenset[str] = frozenset(
    m["id"] for m in TRANSCRIPTION_MODELS
)

PROVIDERS: tuple[str, ...] = ("groq",)


# -- Bounds ------------------------------------------------------------------
#
# (minimum, maximum) for every number a super admin can set. A value outside its
# range is clamped rather than rejected on read, so a row written before a bound
# tightened still produces a working service; the write path refuses it outright
# so the person setting it is told.
#
# The two timeout ceilings are 28 seconds, not 30: the gateway's proxy timeout is
# a fixed 30, and the assistant needs the last couple of seconds to turn a
# provider timeout into its own worded refusal.

BOUNDS: dict[str, tuple[int, int]] = {
    "max_question_chars": (200, 8000),
    "request_timeout_seconds": (5, 28),
    "history_max_conversations": (5, 500),
    "history_max_messages": (10, 2000),
    "max_audio_bytes": (256 * 1024, 25 * 1024 * 1024),
    "max_audio_duration_ms": (5_000, 300_000),
    "voice_timeout_seconds": (5, 28),
    "live_data_cache_seconds": (0, 300),
    "live_data_timeout_seconds": (1, 15),
    "live_data_max_metrics": (1, 10),
}


@dataclass(frozen=True)
class AssistantRuntimeConfig:
    """A resolved configuration.

    Immutable, so a request cannot have its bounds changed underneath it by a
    concurrent save.
    """

    provider: str = "groq"
    api_key: str | None = None
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "openai/gpt-oss-120b"
    transcription_model: str = "whisper-large-v3"

    max_question_chars: int = 2000
    request_timeout_seconds: int = 20

    history_max_conversations: int = 50
    history_max_messages: int = 200

    max_audio_bytes: int = 5 * 1024 * 1024
    max_audio_duration_ms: int = 60_000
    voice_timeout_seconds: int = 20

    live_data_cache_seconds: int = 30
    live_data_timeout_seconds: int = 3
    live_data_max_metrics: int = 3

    updated_by: str | None = None
    updated_at: str | None = None

    # True when the values came from the stored row rather than the built-in
    # defaults. Surfaced in the portal so a super admin can tell "nobody has
    # configured this yet" from "somebody chose these".
    is_stored: bool = False

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key_hint(self) -> str | None:
        """A masked tail: enough to tell two keys apart, useless on its own."""
        if not self.api_key:
            return None
        return f"****{self.api_key[-4:]}"


def _clamp(field: str, value: Any, fallback: int) -> int:
    low, high = BOUNDS[field]
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _defaults_from_environment() -> AssistantRuntimeConfig:
    """Built-in defaults, with the bootstrap environment provider settings
    applied if a deployment still supplies them."""
    return AssistantRuntimeConfig(
        api_key=getattr(settings, "assistant_groq_api_key", None) or None,
        base_url=(
            getattr(settings, "assistant_groq_base_url", "")
            or AssistantRuntimeConfig.base_url
        ),
        model=(
            getattr(settings, "assistant_groq_model", "")
            or AssistantRuntimeConfig.model
        ),
    )


def _from_row(row: AssistantConfig) -> AssistantRuntimeConfig:
    base = _defaults_from_environment()

    api_key = base.api_key
    if row.api_key_encrypted:
        try:
            api_key = decrypt_dsn(row.api_key_encrypted) or None
        except Exception:
            # A key encrypted under a different TENANT_DB_ENCRYPTION_KEY, which
            # is what a restored database or a rotated key looks like. Fall back
            # to the bootstrap value rather than sending ciphertext to a vendor.
            logger.warning("assistant api key could not be decrypted; using bootstrap")

    model = row.model if row.model in CHAT_MODEL_IDS else base.model
    transcription_model = (
        row.transcription_model
        if row.transcription_model in TRANSCRIPTION_MODEL_IDS
        else base.transcription_model
    )

    return replace(
        base,
        provider=(row.provider or base.provider).strip().lower(),
        api_key=api_key,
        base_url=(row.base_url or base.base_url).strip(),
        model=model,
        transcription_model=transcription_model,
        max_question_chars=_clamp(
            "max_question_chars", row.max_question_chars, base.max_question_chars
        ),
        request_timeout_seconds=_clamp(
            "request_timeout_seconds",
            row.request_timeout_seconds,
            base.request_timeout_seconds,
        ),
        history_max_conversations=_clamp(
            "history_max_conversations",
            row.history_max_conversations,
            base.history_max_conversations,
        ),
        history_max_messages=_clamp(
            "history_max_messages", row.history_max_messages, base.history_max_messages
        ),
        max_audio_bytes=_clamp(
            "max_audio_bytes", row.max_audio_bytes, base.max_audio_bytes
        ),
        max_audio_duration_ms=_clamp(
            "max_audio_duration_ms",
            row.max_audio_duration_ms,
            base.max_audio_duration_ms,
        ),
        voice_timeout_seconds=_clamp(
            "voice_timeout_seconds",
            row.voice_timeout_seconds,
            base.voice_timeout_seconds,
        ),
        live_data_cache_seconds=_clamp(
            "live_data_cache_seconds",
            row.live_data_cache_seconds,
            base.live_data_cache_seconds,
        ),
        live_data_timeout_seconds=_clamp(
            "live_data_timeout_seconds",
            row.live_data_timeout_seconds,
            base.live_data_timeout_seconds,
        ),
        live_data_max_metrics=_clamp(
            "live_data_max_metrics",
            row.live_data_max_metrics,
            base.live_data_max_metrics,
        ),
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        is_stored=True,
    )


_lock = threading.Lock()
_cached: AssistantRuntimeConfig | None = None
_cached_at: float = 0.0


def _read_row() -> AssistantRuntimeConfig:
    try:
        with get_master_session() as db:
            # The table is absent until migration 0023 is applied. That is a
            # normal state during a rolling deploy, not an error worth a stack
            # trace on every request.
            if not sa_inspect(db.get_bind()).has_table(AssistantConfig.__tablename__):
                return _defaults_from_environment()
            row = db.get(AssistantConfig, CONFIG_ROW_ID)
            if row is None:
                return _defaults_from_environment()
            return _from_row(row)
    except Exception:
        logger.warning("assistant configuration unavailable; using defaults")
        return _defaults_from_environment()


def get_config(*, refresh: bool = False) -> AssistantRuntimeConfig:
    """The configuration in force for this request.

    Cached for CACHE_TTL_SECONDS. Never raises: every failure path resolves to
    the built-in defaults, so a configuration problem degrades the assistant's
    tuning rather than its availability.
    """
    global _cached, _cached_at

    now = time.monotonic()
    if not refresh and _cached is not None and (now - _cached_at) < CACHE_TTL_SECONDS:
        return _cached

    loaded = _read_row()
    with _lock:
        _cached = loaded
        _cached_at = time.monotonic()
    return loaded


def invalidate_cache() -> None:
    """Drop the cached configuration so the next read reloads the row.

    Called after a save, so the super admin who just pressed Save sees the new
    values on the page they are returned to rather than up to ten seconds later.
    Other replicas pick the change up when their own cache expires.
    """
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


def save_config(values: dict[str, Any], *, actor: str | None) -> AssistantRuntimeConfig:
    """Write the configuration row and return what is now in force.

    `values` carries only the fields the caller intends to change. The API key is
    handled separately from the rest: absent means "leave the stored key alone",
    which is what lets the portal render a masked key it never received.
    """
    with get_master_session() as db:
        row = db.get(AssistantConfig, CONFIG_ROW_ID)
        if row is None:
            row = AssistantConfig(id=CONFIG_ROW_ID)
            db.add(row)

        if "provider" in values:
            row.provider = str(values["provider"]).strip().lower()
        if "base_url" in values:
            row.base_url = str(values["base_url"]).strip()
        if "model" in values:
            row.model = str(values["model"]).strip()
        if "transcription_model" in values:
            row.transcription_model = str(values["transcription_model"]).strip()

        for field in BOUNDS:
            if field in values:
                setattr(row, field, int(values[field]))

        # An explicit clear wins over a supplied key, so "remove the key" cannot
        # be defeated by a stale value still sitting in the form.
        if values.get("clear_api_key"):
            row.api_key_encrypted = None
        elif values.get("api_key"):
            row.api_key_encrypted = encrypt_dsn(str(values["api_key"]).strip())

        row.updated_by = actor

        db.commit()
        db.refresh(row)
        resolved = _from_row(row)

    invalidate_cache()
    return resolved
