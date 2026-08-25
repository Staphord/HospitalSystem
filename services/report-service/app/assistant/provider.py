from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.core.config import settings

# Vendor-neutral seam between the assistant and whichever model provider is
# configured. Groq is the approved vendor for this project, but nothing outside
# this module may import a vendor SDK or read a provider credential.
#
# Phase 1 defines the boundary only. No provider is called from here; the Groq
# transport, server-side secret loading, and provider error normalization are
# phase 2 deliverables, so the factory below resolves to NullProvider.

GROQ = "groq"
NULL = "null"


class ProviderErrorCode:
    NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "PROVIDER_TIMEOUT"
    INVALID_OUTPUT = "INVALID_PROVIDER_OUTPUT"


class AssistantProviderError(Exception):
    """Normalized provider failure.

    Carries a stable code and a short safe message only. Vendor payloads, keys,
    prompts, and stack traces must never be attached to this exception or
    forwarded to a client.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProviderRequest:
    """A vendor-neutral request. Carries only assembled, approved content."""

    instructions: str
    content: str
    max_output_tokens: int = 800
    temperature: float = 0.0
    timeout_seconds: float = field(default=20.0)


@dataclass(frozen=True)
class ProviderResponse:
    """A vendor-neutral response. Text only; no vendor object is exposed."""

    text: str
    model_version: str
    finish_reason: str = "stop"


@runtime_checkable
class AssistantProvider(Protocol):
    """The only interface the assistant may use to reach a model."""

    name: str

    def describe(self) -> dict[str, str]:
        """Return non-secret identification for audit and diagnostics."""
        ...

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Run one completion, or raise AssistantProviderError."""
        ...


class NullProvider:
    """Fail-closed provider used whenever no transport is available.

    This is the phase 1 default. It never performs network access and always
    raises a normalized, safe error rather than returning a fabricated answer.
    """

    name = NULL

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "model_version": "none"}

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise AssistantProviderError(
            ProviderErrorCode.NOT_CONFIGURED,
            "The assistant is not available right now.",
        )


def configured_provider_name() -> str:
    """Return the vendor recorded in configuration, normalized."""
    return (getattr(settings, "assistant_provider", NULL) or NULL).strip().lower()


def is_provider_configured() -> bool:
    """Return whether the configured vendor has a usable server-side credential.

    Only presence is reported. The credential value is never returned, logged,
    included in an audit record, or sent to a client.
    """
    if configured_provider_name() != GROQ:
        return False
    return bool(getattr(settings, "assistant_groq_api_key", None))


def describe_configured_provider() -> dict[str, str]:
    """Return non-secret provider configuration for audit and diagnostics."""
    return {
        "provider": configured_provider_name(),
        "model_version": str(getattr(settings, "assistant_groq_model", "") or "unset"),
        "credential_present": "true" if is_provider_configured() else "false",
    }


def get_provider() -> AssistantProvider:
    """Return the active provider.

    Phase 1 has no transport implementation, so this always returns the
    fail-closed NullProvider. Phase 2 registers the Groq transport here.
    """
    return NullProvider()
