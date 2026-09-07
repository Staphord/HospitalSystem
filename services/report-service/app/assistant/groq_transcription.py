from __future__ import annotations

import logging
from typing import Any

import httpx

from app.assistant.redaction import scrub
from app.assistant.transcription import (
    GROQ,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    TranscriptionResult,
)
from app.core.config import settings

logger = logging.getLogger("service")

# The only module in the service that sends audio to a speech vendor.
#
# The credential is read from server configuration here and nowhere else. It is
# never returned, never logged, never placed in an audit record, and never sent
# to a browser. Audio bytes are held only for the duration of the call: they are
# not written to disk, not cached, and never logged. Vendor errors are
# normalised into the small set of codes defined on the seam, so no vendor
# payload, HTTP body, or stack trace can escape into a client response or an
# ordinary log line.


class GroqTranscriptionProvider:
    """Groq speech-to-text transport implementing the transcription seam."""

    name = GROQ

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        default_timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._default_timeout = default_timeout

    def describe(self) -> dict[str, str]:
        """Return non-secret identification for audit and diagnostics."""
        return {"provider": self.name, "model_version": self._model}

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        data: dict[str, Any] = {
            "model": self._model,
            "response_format": "verbose_json",
            # Deterministic decoding. A non-zero temperature makes the engine
            # more willing to guess at unclear audio, which is how unspoken
            # words appear in a transcript.
            "temperature": "0",
        }
        if request.language:
            data["language"] = request.language

        # No `prompt` is sent. A vocabulary prompt biases the engine towards the
        # words it lists, and a medication or symptom list supplied that way is
        # a direct route to a transcript containing a drug the speaker never
        # said. The transcript stays a record of what was spoken.

        files = {
            "file": (request.filename, request.audio, request.content_type),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout = request.timeout_seconds or self._default_timeout

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    data=data,
                    files=files,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            logger.warning("assistant transcription timeout provider=%s", self.name)
            raise TranscriptionError(
                TranscriptionErrorCode.TIMEOUT,
                "Transcribing that recording took too long.",
            ) from exc
        except httpx.HTTPError as exc:
            # The exception text can carry the request URL; scrub before logging
            # and never forward it to the caller.
            logger.warning(
                "assistant transcription transport error provider=%s detail=%s",
                self.name,
                scrub(type(exc).__name__),
            )
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "Voice input is not available right now.",
            ) from exc

        if response.status_code >= 400:
            # Status only. The vendor body can echo request detail, so it is
            # never logged and never returned.
            logger.warning(
                "assistant transcription rejected provider=%s status=%s",
                self.name,
                response.status_code,
            )
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "Voice input is not available right now.",
            )

        try:
            payload = response.json()
            text = payload.get("text")
            language = payload.get("language")
            duration = payload.get("duration")
        except (ValueError, AttributeError) as exc:
            logger.warning(
                "assistant transcription returned unusable output provider=%s",
                self.name,
            )
            raise TranscriptionError(
                TranscriptionErrorCode.INVALID_OUTPUT,
                "That recording could not be transcribed.",
            ) from exc

        if not isinstance(text, str):
            raise TranscriptionError(
                TranscriptionErrorCode.INVALID_OUTPUT,
                "That recording could not be transcribed.",
            )

        return TranscriptionResult(
            text=text,
            language=str(language) if isinstance(language, str) else None,
            duration_seconds=float(duration)
            if isinstance(duration, (int, float))
            else None,
            model_version=str(payload.get("model") or self._model),
        )


def build_groq_transcription_provider() -> GroqTranscriptionProvider | None:
    """Build the Groq speech transport from configuration, or None if unusable."""
    api_key = getattr(settings, "assistant_groq_api_key", None)
    if not api_key:
        return None
    return GroqTranscriptionProvider(
        api_key=api_key,
        base_url=getattr(settings, "assistant_groq_base_url", "") or "",
        model=getattr(settings, "assistant_transcription_model", "") or "",
        default_timeout=float(
            getattr(settings, "assistant_voice_timeout_seconds", 20.0)
        ),
    )
