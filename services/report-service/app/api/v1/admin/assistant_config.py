"""Super admin configuration for the Hospital Assistant.

Platform scope, not tenant scope. These routes configure the model provider and
the request bounds for every hospital on the platform at once, so they are
reachable only by a platform super admin: a hospital administrator is refused
here exactly as they are refused on tenant provisioning.

The API key is write-only across this boundary. It goes in on a save and never
comes back out - reads return a masked tail and a boolean, which is enough for
someone to confirm which key is loaded and useless to anyone who intercepts it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.assistant.config_store import (
    BOUNDS,
    CHAT_MODELS,
    CHAT_MODEL_IDS,
    PROVIDERS,
    TRANSCRIPTION_MODELS,
    TRANSCRIPTION_MODEL_IDS,
    get_config,
    save_config,
)
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.master import get_master_session
from app.models.master import GlobalAuditLog

logger = logging.getLogger("assistant.admin")

router = APIRouter(tags=["Assistant configuration"])


def require_super_admin(
    ctx: TenantContext = Depends(get_current_tenant),
) -> TenantContext:
    """Refuse anyone who is not a platform super admin.

    Fail-closed and deliberately blunt: there is no tenant-scoped version of
    this configuration, so there is no role short of super admin that should
    reach it. A hospital administrator asking for it is a 403, not a filtered
    view.
    """
    if not getattr(ctx, "is_super_admin", False):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PERMISSION_DENIED",
                "message": "Assistant configuration is managed by the platform super admin.",
            },
        )
    if getattr(ctx, "scope", "full") == "readonly":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "READ_ONLY_SCOPE",
                "message": "Write operations are not allowed in readonly mode",
            },
        )
    return ctx


# -- Contracts ---------------------------------------------------------------


class ModelOption(BaseModel):
    id: str
    label: str
    note: str


class FieldBound(BaseModel):
    """The range the server will accept, sent so the form can enforce the same
    limits the API does rather than guessing them or discovering them on save."""

    minimum: int
    maximum: int


class AssistantConfigResponse(BaseModel):
    """What is in force now. Never carries the API key itself."""

    model_config = ConfigDict(protected_namespaces=())

    provider: str
    api_key_set: bool
    api_key_hint: str | None = None
    base_url: str
    model: str
    transcription_model: str

    max_question_chars: int
    request_timeout_seconds: int

    history_max_conversations: int
    history_max_messages: int

    max_audio_bytes: int
    max_audio_duration_ms: int
    voice_timeout_seconds: int

    live_data_cache_seconds: int
    live_data_timeout_seconds: int
    live_data_max_metrics: int

    is_stored: bool
    updated_by: str | None = None
    updated_at: str | None = None

    # Reference data, so the page can render the whole form from one call.
    providers: list[str] = Field(default_factory=list)
    chat_models: list[ModelOption] = Field(default_factory=list)
    transcription_models: list[ModelOption] = Field(default_factory=list)
    bounds: dict[str, FieldBound] = Field(default_factory=dict)

    # True when the deployment switch is off. The page renders a banner saying
    # the assistant is switched off in the environment, because otherwise a
    # super admin can configure a model perfectly and see nothing happen.
    assistant_enabled: bool = False


class AssistantConfigUpdate(BaseModel):
    """A partial update. Every field is optional; only what is sent is changed.

    The API key follows a different rule from the rest, because the page never
    receives it and so cannot send it back unchanged: omitting it means "keep
    the stored key", and clearing it takes an explicit flag rather than an
    empty string, which is what an untouched form field looks like.
    """

    model_config = ConfigDict(protected_namespaces=())

    provider: str | None = None
    api_key: str | None = Field(default=None, max_length=512)
    clear_api_key: bool = False
    base_url: str | None = Field(default=None, max_length=255)
    model: str | None = None
    transcription_model: str | None = None

    max_question_chars: int | None = None
    request_timeout_seconds: int | None = None
    history_max_conversations: int | None = None
    history_max_messages: int | None = None
    max_audio_bytes: int | None = None
    max_audio_duration_ms: int | None = None
    voice_timeout_seconds: int | None = None
    live_data_cache_seconds: int | None = None
    live_data_timeout_seconds: int | None = None
    live_data_max_metrics: int | None = None

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in PROVIDERS:
            raise ValueError(f"Unknown provider. Choose one of: {', '.join(PROVIDERS)}")
        return normalized

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized not in CHAT_MODEL_IDS:
            # An allowlist, not a free text field: the agentic compound models
            # perform server-side web search and code execution, which the
            # assistant must never reach, and a mistyped id fails opaquely at
            # the provider long after the save.
            raise ValueError("That model is not on the approved list.")
        return normalized

    @field_validator("transcription_model")
    @classmethod
    def _known_transcription_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized not in TRANSCRIPTION_MODEL_IDS:
            raise ValueError("That transcription model is not on the approved list.")
        return normalized

    @field_validator("base_url")
    @classmethod
    def _https_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("The provider base URL must use https.")
        return normalized

    def bounded_values(self) -> dict[str, Any]:
        """The numeric fields that were sent, checked against their ranges.

        Out-of-range values are refused here rather than clamped, so the person
        setting a 60 second timeout is told it cannot exceed 28 instead of
        saving successfully and getting 28.
        """
        values: dict[str, Any] = {}
        for field, (low, high) in BOUNDS.items():
            sent = getattr(self, field, None)
            if sent is None:
                continue
            if not (low <= int(sent) <= high):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_REQUEST",
                        "message": (
                            f"{field.replace('_', ' ')} must be between {low} and {high}."
                        ),
                    },
                )
            values[field] = int(sent)
        return values


class ProviderTestResult(BaseModel):
    ok: bool
    message: str
    model: str | None = None


# -- Routes ------------------------------------------------------------------


def _to_response(config, *, assistant_enabled: bool) -> AssistantConfigResponse:
    return AssistantConfigResponse(
        provider=config.provider,
        api_key_set=config.api_key_set,
        api_key_hint=config.api_key_hint,
        base_url=config.base_url,
        model=config.model,
        transcription_model=config.transcription_model,
        max_question_chars=config.max_question_chars,
        request_timeout_seconds=config.request_timeout_seconds,
        history_max_conversations=config.history_max_conversations,
        history_max_messages=config.history_max_messages,
        max_audio_bytes=config.max_audio_bytes,
        max_audio_duration_ms=config.max_audio_duration_ms,
        voice_timeout_seconds=config.voice_timeout_seconds,
        live_data_cache_seconds=config.live_data_cache_seconds,
        live_data_timeout_seconds=config.live_data_timeout_seconds,
        live_data_max_metrics=config.live_data_max_metrics,
        is_stored=config.is_stored,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
        providers=list(PROVIDERS),
        chat_models=[ModelOption(**m) for m in CHAT_MODELS],
        transcription_models=[ModelOption(**m) for m in TRANSCRIPTION_MODELS],
        bounds={
            field: FieldBound(minimum=low, maximum=high)
            for field, (low, high) in BOUNDS.items()
        },
        assistant_enabled=assistant_enabled,
    )


def _record(ctx: TenantContext, action: str, detail: str, request: Request) -> None:
    """Write one platform audit row.

    Records that the configuration changed and which fields, never a value that
    could be a secret. There is no branch here that can reach the API key.
    """
    try:
        with get_master_session() as db:
            db.add(
                GlobalAuditLog(
                    tenant_id=None,
                    user_sub=getattr(ctx, "user_sub", None),
                    action=action,
                    detail=detail,
                    ip_address=request.client.host if request.client else None,
                )
            )
            db.commit()
    except Exception:
        # An audit write must not cost the super admin their change. It is
        # logged instead, and the row is reconstructable from the service log.
        logger.warning("assistant config audit row could not be written")


@router.get(
    "/admin/assistant-config",
    response_model=AssistantConfigResponse,
    summary="Read the platform assistant configuration",
)
@limiter.limit("60/minute")
async def read_assistant_config(
    request: Request,
    ctx: TenantContext = Depends(require_super_admin),
):
    from app.assistant.flags import is_assistant_enabled

    # Read past the cache: a super admin looking at this page wants what is
    # actually stored, not what a request ten seconds ago happened to load.
    return _to_response(
        get_config(refresh=True), assistant_enabled=is_assistant_enabled()
    )


@router.put(
    "/admin/assistant-config",
    response_model=AssistantConfigResponse,
    summary="Update the platform assistant configuration",
)
@limiter.limit("20/minute")
async def update_assistant_config(
    request: Request,
    payload: AssistantConfigUpdate,
    ctx: TenantContext = Depends(require_super_admin),
):
    from app.assistant.flags import is_assistant_enabled

    values: dict[str, Any] = payload.bounded_values()

    for field in ("provider", "base_url", "model", "transcription_model"):
        sent = getattr(payload, field)
        if sent is not None:
            values[field] = sent

    if payload.clear_api_key:
        values["clear_api_key"] = True
    elif payload.api_key and payload.api_key.strip():
        values["api_key"] = payload.api_key.strip()

    if not values:
        return _to_response(
            get_config(refresh=True), assistant_enabled=is_assistant_enabled()
        )

    actor = getattr(ctx, "preferred_username", None) or getattr(ctx, "user_sub", None)
    updated = save_config(values, actor=actor)

    # Field names only. "api_key" here means the key was replaced, never what
    # it was replaced with.
    changed = ", ".join(sorted(values))
    _record(ctx, "assistant_config_updated", f"fields: {changed}", request)

    return _to_response(updated, assistant_enabled=is_assistant_enabled())


@router.post(
    "/admin/assistant-config/test",
    response_model=ProviderTestResult,
    summary="Check the stored credential against the provider",
)
@limiter.limit("6/minute")
async def test_assistant_provider(
    request: Request,
    ctx: TenantContext = Depends(require_super_admin),
):
    """One cheap call to the provider, so a wrong key is found here rather than
    by a nurse asking a question.

    It lists models rather than generating anything: no prompt is sent, nothing
    is billed for tokens, and no hospital data is involved. The vendor's own
    error text is not forwarded - it can carry request identifiers and account
    detail that has no business in a browser.
    """
    config = get_config(refresh=True)

    if not config.api_key:
        return ProviderTestResult(
            ok=False, message="No API key is set for the assistant."
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{config.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {config.api_key}"},
            )
    except httpx.TimeoutException:
        return ProviderTestResult(ok=False, message="The provider did not respond in time.")
    except Exception:
        logger.warning("assistant provider test could not reach the provider")
        return ProviderTestResult(ok=False, message="The provider could not be reached.")

    if response.status_code in (401, 403):
        return ProviderTestResult(ok=False, message="The provider rejected this API key.")
    if response.status_code >= 400:
        return ProviderTestResult(
            ok=False,
            message=f"The provider returned an error ({response.status_code}).",
        )

    # The key works. Whether the chosen model is actually in this account's
    # catalogue is the more useful question, so it is answered here rather than
    # left to fail on the first real question.
    served: set[str] = set()
    try:
        for entry in response.json().get("data", []):
            identifier = entry.get("id")
            if isinstance(identifier, str):
                served.add(identifier)
    except Exception:
        served = set()

    if served and config.model not in served:
        return ProviderTestResult(
            ok=False,
            message=(
                f"The key works, but this account does not serve {config.model}. "
                "Choose a different model."
            ),
            model=config.model,
        )

    return ProviderTestResult(
        ok=True, message="The provider accepted this key.", model=config.model
    )
