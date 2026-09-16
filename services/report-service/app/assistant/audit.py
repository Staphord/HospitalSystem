from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from app.assistant.contracts import StrictModel
from app.assistant.flags import AssistantCapability

# Field names that must never appear in an assistant audit record. Audit answers
# "who asked what kind of question, against which versions, with what outcome" —
# never the question text, the answer text, the transcript, or the audio.
PROHIBITED_AUDIT_FIELDS: frozenset[str] = frozenset(
    {
        "question",
        "answer",
        "prompt",
        "system_prompt",
        "transcript",
        "audio",
        "audio_bytes",
        "content",
        "message",
        "messages",
        "tool_payload",
        "tool_result",
        "api_key",
        "groq_api_key",
        "secret_key",
        "database_url",
        "db_dsn",
        "stack_trace",
        "traceback",
        "allergies",
        "diagnosis",
        "notes",
    }
)


class AssistantOutcome(str, Enum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission_denied"
    CAPABILITY_DISABLED = "capability_disabled"
    PROVIDER_ERROR = "provider_error"
    NEEDS_REVIEW = "needs_review"
    INVALID_REQUEST = "invalid_request"


class AssistantAuditMetadata(StrictModel):
    """Audit record for one assistant interaction.

    Identifiers and versions only. Patient and visit references are opaque ids
    recorded solely so an interaction can be traced; no clinical content, no free
    text from the user, and no provider payload is carried here.

    The generic HTTP audit row is already written by AuditLogMiddleware, which
    also owns request_id. This record carries the assistant-specific fields that
    the middleware cannot know, and reuses the same request_id.

    Tenant and provider identity are recorded here because the server resolved
    them, so this model uses the strict base rather than the client-boundary
    base that refuses those names.
    """

    request_id: str = Field(min_length=1, max_length=64)
    actor_sub: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=64)
    capability: AssistantCapability
    outcome: AssistantOutcome
    patient_ref: str | None = Field(default=None, max_length=64)
    visit_ref: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=40)
    model_version: str | None = Field(default=None, max_length=80)
    ruleset_version: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _reject_content_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if isinstance(key, str) and key.strip().lower() in PROHIBITED_AUDIT_FIELDS:
                    raise ValueError(
                        "PROHIBITED_AUDIT_FIELD: "
                        + str(key)
                        + " must never be written to an assistant audit record"
                    )
        return data


def build_audit_metadata(
    request_id: str,
    actor_sub: str,
    capability: AssistantCapability,
    outcome: AssistantOutcome,
    tenant_id: str | None = None,
    patient_ref: str | None = None,
    visit_ref: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    ruleset_version: str | None = None,
    duration_ms: int | None = None,
) -> AssistantAuditMetadata:
    """Build an audit record from server-resolved values only.

    Callers must pass the request id created by AuditLogMiddleware and the actor
    and tenant taken from the verified token context, never values supplied by
    the client.
    """
    return AssistantAuditMetadata(
        request_id=request_id,
        actor_sub=actor_sub,
        tenant_id=tenant_id,
        capability=capability,
        outcome=outcome,
        patient_ref=patient_ref,
        visit_ref=visit_ref,
        provider=provider,
        model_version=model_version,
        ruleset_version=ruleset_version,
        duration_ms=duration_ms,
    )
