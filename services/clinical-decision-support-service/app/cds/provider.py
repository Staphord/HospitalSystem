"""The only seam between clinical decision support and a model vendor.

Nothing outside this module may import a vendor SDK or read a provider
credential. That is the whole point of the boundary: the rest of the service is
written against `CdsProvider`, so swapping vendors, or removing the model
entirely, touches one file.

What crosses this seam is tightly bounded on both sides:

- **Outbound** is assembled, allowlisted text only. No database credential, no
  tenant header, no SQL, no HTTP capability, no tool, and no patient identifier.
  The caller is responsible for what it assembles; this module never fetches.
- **Inbound** is text. It is parsed and schema-validated by the caller, and the
  contracts reject anything that treats, doses, refers, or asserts a number.

The model never decides severity, never produces a red flag, and never gates
anything. With no key configured the null provider answers, and it fabricates
nothing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import settings

logger = logging.getLogger("cds.provider")

GROQ = "groq"
NULL = "null"


class ProviderErrorCode:
    NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "PROVIDER_TIMEOUT"
    INVALID_OUTPUT = "INVALID_PROVIDER_OUTPUT"


class CdsProviderError(Exception):
    """Normalized provider failure.

    Carries a stable code and a short safe message only. Vendor payloads, keys,
    prompts, and stack traces are never attached and never forwarded to a client.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProviderRequest:
    """A vendor-neutral request carrying assembled, approved content only."""

    instructions: str
    content: str
    max_output_tokens: int = 1200
    temperature: float = 0.0
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class ProviderResponse:
    """A vendor-neutral response. Text only; no vendor object is exposed."""

    text: str
    model_version: str
    finish_reason: str = "stop"


@runtime_checkable
class CdsProvider(Protocol):
    """The only interface clinical decision support may use to reach a model."""

    name: str

    def describe(self) -> dict[str, str]:
        """Return non-secret identification for audit and diagnostics."""
        ...

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Run one completion, or raise CdsProviderError."""
        ...


class NullProvider:
    """Fail-closed provider used whenever no transport is configured.

    Never performs network access and always raises a normalized error rather
    than returning a fabricated suggestion. A deployment with no key gets no
    differential output at all, which is the correct behaviour: an invented
    consideration is worse than no consideration.
    """

    name = NULL

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "model_version": "none"}

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise CdsProviderError(
            ProviderErrorCode.NOT_CONFIGURED,
            "No model provider is configured for clinical decision support.",
        )


class GroqProvider:
    """The approved vendor transport. The only place a provider key is read."""

    name = GROQ

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    def describe(self) -> dict[str, str]:
        # Deliberately no key, no prefix of a key, and no header.
        return {"provider": self.name, "model_version": self._model}

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "model": self._model,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise CdsProviderError(
                ProviderErrorCode.TIMEOUT, "The model provider timed out."
            ) from exc
        except httpx.HTTPError as exc:
            # The vendor's own error text is deliberately not carried forward.
            logger.warning("cds provider transport error: %s", type(exc).__name__)
            raise CdsProviderError(
                ProviderErrorCode.UNAVAILABLE, "The model provider is unavailable."
            ) from exc

        if response.status_code >= 400:
            logger.warning("cds provider returned status %s", response.status_code)
            raise CdsProviderError(
                ProviderErrorCode.UNAVAILABLE, "The model provider is unavailable."
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = str(choice.get("finish_reason") or "stop")
            model_version = str(data.get("model") or self._model)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise CdsProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "The model provider returned an unusable response.",
            ) from exc

        if not isinstance(text, str) or not text.strip():
            raise CdsProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "The model provider returned an empty response.",
            )

        return ProviderResponse(
            text=text, model_version=model_version, finish_reason=finish_reason
        )


def build_provider() -> CdsProvider:
    """Resolve the configured provider, falling back to the null provider.

    Fail-closed in every direction: an unknown provider name, a missing key, or
    a missing model all resolve to NullProvider rather than to a guess.
    """
    configured = str(getattr(settings, "cds_provider", NULL) or NULL).strip().lower()
    if configured != GROQ:
        return NullProvider()

    api_key = getattr(settings, "cds_groq_api_key", None)
    model = str(getattr(settings, "cds_groq_model", "") or "")
    if not api_key or not model:
        return NullProvider()

    return GroqProvider(
        api_key=str(api_key),
        base_url=str(getattr(settings, "cds_groq_base_url", "") or ""),
        model=model,
    )
