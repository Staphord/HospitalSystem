from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import settings

# Vendor-neutral seam between the assistant and whichever speech-to-text engine
# is configured. It mirrors app.assistant.provider deliberately: nothing outside
# a transport module may import a vendor SDK or read a provider credential, and
# swapping Groq for a self-hosted engine is a configuration change plus one new
# transport, not a rewrite of the voice feature.

GROQ = "groq"
NULL = "null"

# Languages the assistant accepts as a recognition hint. A hint is optional; the
# engine auto-detects when none is given, which is what handles a staff member
# switching between Swahili and English inside one sentence.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "sw"})


class TranscriptionErrorCode:
    NOT_CONFIGURED = "TRANSCRIPTION_NOT_CONFIGURED"
    UNAVAILABLE = "TRANSCRIPTION_UNAVAILABLE"
    TIMEOUT = "TRANSCRIPTION_TIMEOUT"
    INVALID_OUTPUT = "INVALID_TRANSCRIPTION_OUTPUT"


class TranscriptionError(Exception):
    """Normalized speech-to-text failure.

    Carries a stable code and a short safe message only. Vendor payloads, keys,
    audio bytes, and stack traces must never be attached to it.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TranscriptionRequest:
    """One capture to transcribe. The audio never leaves this object."""

    audio: bytes
    content_type: str
    filename: str
    language: str | None = None
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class TranscriptionResult:
    """A vendor-neutral transcript. Text only; no vendor object is exposed."""

    text: str
    language: str | None
    duration_seconds: float | None
    model_version: str


def normalize_language(value: str | None) -> str | None:
    """Reduce a requested language hint to a supported code, or None.

    Fail-open to auto-detection rather than to a wrong language: an unknown or
    malformed hint becomes None, which lets the engine detect the language
    itself instead of being told something untrue.
    """
    if not value or not isinstance(value, str):
        return None
    code = value.strip().lower()[:5]
    if code in SUPPORTED_LANGUAGES:
        return code
    return None


# Whisper-family models emit a small set of stock phrases when handed silence or
# pure noise, because those phrases are frequent in their subtitle training
# data. Treating one of them as speech would put words in a staff member's mouth
# that they never said, which is exactly the failure this phase must not have.
# A transcript consisting only of such an artifact is reported as "nothing was
# heard" rather than returned as a transcript.
_SILENCE_ARTIFACTS: frozenset[str] = frozenset(
    {
        "thank you",
        "thanks for watching",
        "thank you for watching",
        "you",
        "bye",
        "bye bye",
        "subtitles by the amara.org community",
        "subs by www.zeoranger.co.uk",
        "amara.org",
        "asante",
        "asante sana",
        "transcription by castingwords",
        "please subscribe",
        "the end",
    }
)

_ARTIFACT_STRIP = re.compile(r"[\s\.,!\?\-–—\"'“”\(\)\[\]]+")


def is_silence_artifact(text: str) -> bool:
    """Return whether a transcript is only a known silence hallucination."""
    if not text:
        return True
    collapsed = _ARTIFACT_STRIP.sub(" ", text.strip().lower()).strip()
    if not collapsed:
        return True
    return collapsed in {
        _ARTIFACT_STRIP.sub(" ", artifact).strip() for artifact in _SILENCE_ARTIFACTS
    }


# The vendor reports which language it detected, as free text it chooses. Groq
# answers "Swahili (macrolanguage)" for Swahili, which is longer than the
# response contract allows and is vendor wording rather than anything a staff
# member needs to read. It is treated like any other vendor output: normalised
# to a plain name, stripped of punctuation, and clamped, so no vendor string
# reaches the browser unshaped and no length surprise can fail a response.
MAX_DETECTED_LANGUAGE_CHARS = 16

_LANGUAGE_NOISE = re.compile(r"[^a-z\- ]+")


def normalize_detected_language(value: str | None) -> str | None:
    """Reduce a vendor language label to a short, safe name."""
    if not value or not isinstance(value, str):
        return None

    # "Swahili (macrolanguage)" -> "Swahili"
    name = value.split("(", 1)[0].strip().lower()
    name = _LANGUAGE_NOISE.sub("", name).strip()
    if not name:
        return None
    return name[:MAX_DETECTED_LANGUAGE_CHARS].strip() or None


@runtime_checkable
class TranscriptionProvider(Protocol):
    """The only interface the assistant may use to reach a speech engine."""

    name: str

    def describe(self) -> dict[str, str]:
        """Return non-secret identification for audit and diagnostics."""
        ...

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Transcribe one capture, or raise TranscriptionError."""
        ...


class NullTranscriptionProvider:
    """Fail-closed engine used whenever no transport is available.

    It never performs network access and always raises a normalized, safe error
    rather than returning a fabricated transcript. A missing credential must
    produce "voice is unavailable", never invented words.
    """

    name = NULL

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "model_version": "none"}

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        raise TranscriptionError(
            TranscriptionErrorCode.NOT_CONFIGURED,
            "Voice input is not available right now.",
        )


def configured_transcription_provider_name() -> str:
    """Return the speech vendor recorded in configuration, normalized."""
    return (getattr(settings, "assistant_provider", NULL) or NULL).strip().lower()


def is_transcription_configured() -> bool:
    """Return whether the configured engine has a usable server-side credential.

    Only presence is reported. The credential value is never returned, logged,
    included in an audit record, or sent to a client.
    """
    if configured_transcription_provider_name() != GROQ:
        return False
    return bool(getattr(settings, "assistant_groq_api_key", None))


def describe_configured_transcription() -> dict[str, str]:
    """Return non-secret speech configuration for audit and diagnostics."""
    return {
        "provider": configured_transcription_provider_name(),
        "model_version": str(
            getattr(settings, "assistant_transcription_model", "") or "unset"
        ),
        "credential_present": "true" if is_transcription_configured() else "false",
    }


def get_transcription_provider() -> TranscriptionProvider:
    """Return the active speech engine.

    Resolves to the configured vendor transport when a server-side credential is
    present, and to the fail-closed null engine otherwise. The transport is
    imported lazily so this seam stays free of any vendor import.
    """
    if not is_transcription_configured():
        return NullTranscriptionProvider()

    from app.assistant.groq_transcription import build_groq_transcription_provider

    return build_groq_transcription_provider() or NullTranscriptionProvider()
