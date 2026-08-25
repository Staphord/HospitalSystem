from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Fields that are server-authoritative or secret and must never be accepted from
# a browser, a prompt, a voice transcript, or a model response. Tenant identity,
# roles, database routing, and provider credentials are resolved from the
# verified token and server configuration only.
FORBIDDEN_CLIENT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant",
        "tenant_id",
        "tenant_db",
        "x_tenant_db",
        "hospital_id",
        "db",
        "database",
        "database_url",
        "db_dsn",
        "db_dsn_encrypted",
        "role",
        "roles",
        "scope",
        "scopes",
        "permissions",
        "is_super_admin",
        "user_sub",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "provider",
        "provider_key",
        "groq_api_key",
        "secret_key",
        "system_prompt",
        "prompt",
        "tools",
        "tool_names",
        "sql",
        "query",
        "url",
        "endpoint",
    }
)


class StrictModel(BaseModel):
    """Base for every assistant model. Unknown fields are rejected outright."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssistantModel(StrictModel):
    """Base for contracts that carry data crossing the client boundary.

    In addition to rejecting unknown fields, the server-authoritative and secret
    field names above are rejected with an explicit code, so the refusal stays
    obvious in tests and logs even if `extra` is ever relaxed.

    Server-built records that legitimately hold resolved tenant or provider
    identity extend StrictModel instead; see app.assistant.audit.
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_server_authoritative_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if isinstance(key, str) and key.strip().lower() in FORBIDDEN_CLIENT_FIELDS:
                    raise ValueError(
                        "FORBIDDEN_CLIENT_FIELD: "
                        + str(key)
                        + " is server-authoritative and is never accepted from the client"
                    )
        return data


# Operational chat


class AssistantChatRequest(AssistantModel):
    """A single operational question from an authenticated staff user."""

    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    locale: str | None = Field(default=None, max_length=16)


class AssistantSource(AssistantModel):
    """A label identifying where an answer segment came from."""

    label: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    version: str | None = Field(default=None, max_length=64)


class AssistantAnswerStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class AssistantChatResponse(AssistantModel):
    """Validated envelope returned to the browser.

    The answer is plain text or tightly controlled Markdown. Raw model HTML is
    never returned and never rendered.
    """

    request_id: str = Field(min_length=1, max_length=64)
    status: AssistantAnswerStatus
    answer: str = Field(max_length=8000)
    sources: list[AssistantSource] = Field(default_factory=list, max_length=20)
    follow_ups: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _unsupported_answers_carry_no_sources(self) -> AssistantChatResponse:
        if self.status is not AssistantAnswerStatus.SUPPORTED and self.sources:
            raise ValueError("only a supported answer may cite sources")
        return self


class AssistantFeedbackRating(str, Enum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    INCORRECT = "incorrect"


class AssistantFeedbackRequest(AssistantModel):
    """Feedback on one previously returned response, scoped by request id."""

    request_id: str = Field(min_length=1, max_length=64)
    rating: AssistantFeedbackRating
    comment: str | None = Field(default=None, max_length=1000)


# Voice metadata. Audio bytes are never described by this contract, and raw audio
# is not retained by default.


class VoiceTranscriptMetadata(AssistantModel):
    """Non-content metadata about one push-to-talk capture."""

    duration_ms: int = Field(gt=0, le=60_000)
    mime_type: str = Field(min_length=1, max_length=80)
    sample_rate_hz: int = Field(gt=0, le=192_000)
    byte_size: int = Field(gt=0, le=10 * 1024 * 1024)
    audio_retained: bool = False
    transcript_confirmed_by_user: bool = False

    @field_validator("audio_retained")
    @classmethod
    def _audio_is_not_retained_by_default(cls, value: bool) -> bool:
        if value:
            raise ValueError("raw audio retention requires a separate approved policy")
        return value


# Medication safety. Severity, and whether two medicines interact at all, are
# decided by an approved versioned deterministic source, never by a model.


class MedicationCheckStatus(str, Enum):
    ALERTS_FOUND = "alerts_found"
    NO_ALERTS_FROM_RULESET = "no_alerts_from_ruleset"
    NEEDS_REVIEW = "needs_review"


class MedicationAlert(AssistantModel):
    kind: str = Field(min_length=1, max_length=40)
    severity: str = Field(min_length=1, max_length=40)
    detail: str = Field(min_length=1, max_length=1000)
    recommendation: str = Field(min_length=1, max_length=1000)
    ruleset_version: str = Field(min_length=1, max_length=64)


class MedicationCheckResult(AssistantModel):
    """Outcome of a deterministic medication check.

    The default is needs_review. Unknown, stale, ambiguous, unavailable, and
    failed checks must stay needs_review and must never be presented as safe or
    as no interaction found.
    """

    status: MedicationCheckStatus = MedicationCheckStatus.NEEDS_REVIEW
    ruleset_version: str | None = Field(default=None, max_length=64)
    alerts: list[MedicationAlert] = Field(default_factory=list, max_length=100)
    needs_review_reason: str | None = Field(default=None, max_length=500)
    checked_at: datetime | None = None

    @model_validator(mode="after")
    def _enforce_fail_closed_semantics(self) -> MedicationCheckResult:
        if self.status is MedicationCheckStatus.NEEDS_REVIEW:
            if not self.needs_review_reason:
                raise ValueError("needs_review requires a reason")
            return self

        if not self.ruleset_version:
            raise ValueError("a concluded medication check requires a ruleset version")
        if self.status is MedicationCheckStatus.ALERTS_FOUND and not self.alerts:
            raise ValueError("alerts_found requires at least one alert")
        if self.status is MedicationCheckStatus.NO_ALERTS_FROM_RULESET and self.alerts:
            raise ValueError("no_alerts_from_ruleset must not carry alerts")
        return self


# Clinical differential support. Reviewable considerations only, never treatment.


class ClinicalSuggestion(AssistantModel):
    consideration: str = Field(min_length=1, max_length=1000)
    supporting_findings: list[str] = Field(default_factory=list, max_length=20)
    contradicting_findings: list[str] = Field(default_factory=list, max_length=20)
    evidence_version: str = Field(min_length=1, max_length=64)


class ClinicalDifferentialSupport(AssistantModel):
    """Diagnosis suggestions for clinician review.

    Carries the inputs used, the evidence version, what data was missing, what
    contradicts the considerations, and the stated limitations. It never
    prescribes, changes a dose, issues an emergency directive, or writes to a
    record.
    """

    request_id: str = Field(min_length=1, max_length=64)
    inputs_used: list[str] = Field(min_length=1, max_length=50)
    suggestions: list[ClinicalSuggestion] = Field(default_factory=list, max_length=20)
    missing_data: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(min_length=1, max_length=20)
    ruleset_version: str = Field(min_length=1, max_length=64)
    requires_human_review: bool = True

    @field_validator("requires_human_review")
    @classmethod
    def _human_review_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("clinical differential support always requires human review")
        return value


# Errors


class AssistantErrorCode(str, Enum):
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    INVALID_PROVIDER_OUTPUT = "INVALID_PROVIDER_OUTPUT"
    UNSUPPORTED_QUESTION = "UNSUPPORTED_QUESTION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AssistantErrorResponse(AssistantModel):
    """Safe, user-facing failure envelope.

    Carries a stable code and a short message only. Provider payloads, prompts,
    database errors, and stack traces are never placed in this envelope.
    """

    request_id: str = Field(min_length=1, max_length=64)
    code: AssistantErrorCode
    message: str = Field(min_length=1, max_length=300)
