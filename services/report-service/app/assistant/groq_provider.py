from __future__ import annotations

import logging
from typing import Any

import httpx

from app.assistant.provider import (
    GROQ,
    AssistantProviderError,
    ProviderErrorCode,
    ProviderRequest,
    ProviderResponse,
)
from app.assistant.redaction import scrub
from app.core.config import settings

logger = logging.getLogger("service")

# The only module in the service that talks to a model vendor.
#
# The credential is read from server configuration here and nowhere else. It is
# never returned, never logged, never placed in an audit record, and never sent
# to a browser. Vendor errors are normalised into the small set of codes defined
# on the seam, so no vendor payload, HTTP body, or stack trace can escape into a
# client response or an ordinary log line.


class GroqProvider:
    """Groq chat-completions transport implementing the assistant seam."""

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

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.content},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = request.timeout_seconds or self._default_timeout

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            logger.warning("assistant provider timeout provider=%s", self.name)
            raise AssistantProviderError(
                ProviderErrorCode.TIMEOUT,
                "The assistant took too long to respond.",
            ) from exc
        except httpx.HTTPError as exc:
            # The exception text can carry the request URL; scrub before logging
            # and never forward it to the caller.
            logger.warning(
                "assistant provider transport error provider=%s detail=%s",
                self.name,
                scrub(type(exc).__name__),
            )
            raise AssistantProviderError(
                ProviderErrorCode.UNAVAILABLE,
                "The assistant is not available right now.",
            ) from exc

        if response.status_code >= 400:
            # Status only. The vendor body may echo the prompt, so it is never
            # logged and never returned.
            logger.warning(
                "assistant provider rejected request provider=%s status=%s",
                self.name,
                response.status_code,
            )
            raise AssistantProviderError(
                ProviderErrorCode.UNAVAILABLE,
                "The assistant is not available right now.",
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason") or "stop"
            model_version = data.get("model") or self._model
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "assistant provider returned unusable output provider=%s", self.name
            )
            raise AssistantProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "The assistant could not produce a usable answer.",
            ) from exc

        if not isinstance(text, str) or not text.strip():
            raise AssistantProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "The assistant could not produce a usable answer.",
            )

        return ProviderResponse(
            text=text,
            model_version=str(model_version),
            finish_reason=str(finish_reason),
        )


def build_groq_provider() -> GroqProvider | None:
    """Build the Groq provider from server configuration, or None if unusable."""
    api_key = getattr(settings, "assistant_groq_api_key", None)
    if not api_key:
        return None
    return GroqProvider(
        api_key=api_key,
        base_url=getattr(settings, "assistant_groq_base_url", "") or "",
        model=getattr(settings, "assistant_groq_model", "") or "",
        default_timeout=float(
            getattr(settings, "assistant_request_timeout_seconds", 20.0)
        ),
    )
