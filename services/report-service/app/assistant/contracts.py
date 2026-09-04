from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any
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
    # The thread this exchange was stored in, so the browser can carry on the
    # same conversation. None when history is switched off or could not be
    # written: an answer is never withheld because history failed.
    conversation_id: UUID | None = None

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


# Chat history
#
# A stored conversation is the caller's own past questions and the answers they
# were given, nothing more. Every contract below is a response contract: a
# conversation is created by asking a question, never by a browser describing
# one, so there is no request body here that could set a title, an author, or an
# owner. The only thing a client sends is a conversation id in the path, and the
# server resolves that against the caller's own rows.


class AssistantMessageAuthor(str, Enum):
    """Who wrote a stored message.

    Deliberately not called a role: the caller's hospital role is resolved from
    the token on every request and is never stored alongside a message, where it
    would go stale and start being read as an authorization fact.
    """

    USER = "user"
    ASSISTANT = "assistant"


class AssistantConversationSummary(AssistantModel):
    """One row in the history list."""

    conversation_id: UUID
    # Derived on the server from the opening question and stored plain, so the
    # list can never be a route for markup the answer sanitizer already refused.
    title: str = Field(min_length=1, max_length=120)
    message_count: int = Field(ge=0)
    created_at: datetime
    last_message_at: datetime


class AssistantSuggestion(AssistantModel):
    """One starting question this caller can actually get an answer to."""

    question: str = Field(min_length=1, max_length=200)
    # "content" or "live_metric", so the panel can group a how-do-I apart from a
    # figure. It is a label for presentation only and names no entry or metric:
    # the browser is never told which content or which metric backs a question.
    kind: str = Field(min_length=1, max_length=40)


class AssistantSuggestionsResponse(AssistantModel):
    """Starting questions, chosen for the caller's own roles.

    Every question here is one the server has already established this caller can
    reach: it comes from a content entry they may read, or a live metric their
    roles pass. An empty list is a real answer - a caller with no usable role is
    offered nothing rather than something that would be refused.
    """

    request_id: str = Field(min_length=1, max_length=64)
    suggestions: list[AssistantSuggestion] = Field(
        default_factory=list, max_length=20
    )


class AssistantConversationListResponse(AssistantModel):
    """The caller's own conversations, most recently used first."""

    conversations: list[AssistantConversationSummary] = Field(
        default_factory=list, max_length=200
    )


class AssistantStoredMessage(AssistantModel):
    """One question or one answer, as it was shown at the time."""

    message_id: UUID
    author: AssistantMessageAuthor
    body: str = Field(max_length=8000)
    # Set on an assistant message only; a question has no answer status and
    # cites nothing.
    answer_status: AssistantAnswerStatus | None = None
    sources: list[AssistantSource] = Field(default_factory=list, max_length=20)
    request_id: str | None = Field(default=None, max_length=64)
    created_at: datetime

    @model_validator(mode="after")
    def _only_answers_carry_answer_fields(self) -> AssistantStoredMessage:
        if self.author is AssistantMessageAuthor.USER and (
            self.answer_status is not None or self.sources
        ):
            raise ValueError("a stored question carries no answer status and no sources")
        return self


class AssistantConversationResponse(AssistantModel):
    """One reopened conversation and its messages, oldest first."""

    conversation_id: UUID
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime
    last_message_at: datetime
    messages: list[AssistantStoredMessage] = Field(default_factory=list, max_length=500)
    # Where the thread could go next, for the answer it currently ends on.
    #
    # Computed when the thread is reopened rather than stored with it, because
    # what a caller may ask is decided by the roles on their token now, not by
    # the roles they held when the exchange happened. A stored follow-up could
    # outlive the permission that made it answerable; this one cannot.
    follow_ups: list[str] = Field(default_factory=list, max_length=5)


# Voice metadata. Audio bytes are never described by this contract, and raw audio
# is not retained by default.


class VoiceTranscriptMetadata(AssistantModel):
    """Non-content metadata about one push-to-talk capture.

    Every value here is determined by the server from the audio bytes
    themselves; nothing is accepted from the browser. Duration and sample rate
    are optional because some containers a browser produces genuinely do not
    carry them, and reporting "not determined" is honest where inventing a
    number would not be. A capture whose length cannot be determined is bounded
    by the byte limit instead, and that is recorded in duration_source.
    """

    duration_ms: Annotated[int, Field(gt=0, le=60_000)] | None = None
    mime_type: str = Field(min_length=1, max_length=80)
    sample_rate_hz: Annotated[int, Field(gt=0, le=192_000)] | None = None
    byte_size: int = Field(gt=0, le=10 * 1024 * 1024)
    container: str | None = Field(default=None, max_length=16)
    codec: str | None = Field(default=None, max_length=16)
    duration_source: str | None = Field(default=None, max_length=16)
    audio_retained: bool = False
    transcript_confirmed_by_user: bool = False

    @field_validator("audio_retained")
    @classmethod
    def _audio_is_not_retained_by_default(cls, value: bool) -> bool:
        if value:
            raise ValueError("raw audio retention requires a separate approved policy")
        return value


class VoiceTranscriptStatus(str, Enum):
    """Whether a capture produced usable speech."""

    TRANSCRIBED = "transcribed"
    NO_SPEECH_DETECTED = "no_speech_detected"


class AssistantVoiceTranscriptResponse(AssistantModel):
    """A transcript returned for the speaker to read, correct, and confirm.

    This response is the end of the voice pipeline, not a step inside another
    one. The server never forwards a transcript onwards on the user's behalf:
    requires_confirmation is structurally always true, so a transcript can only
    become a question after the person who spoke it has seen it and sent it.
    """

    request_id: str = Field(min_length=1, max_length=64)
    status: VoiceTranscriptStatus
    transcript: str = Field(default="", max_length=4000)
    language: str | None = Field(default=None, max_length=16)
    metadata: VoiceTranscriptMetadata
    requires_confirmation: bool = True

    @field_validator("requires_confirmation")
    @classmethod
    def _confirmation_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("a transcript always requires explicit user confirmation")
        return value

    @model_validator(mode="after")
    def _no_speech_carries_no_transcript(self) -> AssistantVoiceTranscriptResponse:
        if self.status is VoiceTranscriptStatus.NO_SPEECH_DETECTED and self.transcript:
            raise ValueError("no_speech_detected must not carry a transcript")
        if self.status is VoiceTranscriptStatus.TRANSCRIBED and not self.transcript:
            raise ValueError("transcribed requires a transcript")
        return self


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
    INVALID_AUDIO = "INVALID_AUDIO"
    AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
    UNSUPPORTED_AUDIO_FORMAT = "UNSUPPORTED_AUDIO_FORMAT"


class AssistantErrorResponse(AssistantModel):
    """Safe, user-facing failure envelope.

    Carries a stable code and a short message only. Provider payloads, prompts,
    database errors, and stack traces are never placed in this envelope.
    """

    request_id: str = Field(min_length=1, max_length=64)
    code: AssistantErrorCode
    message: str = Field(min_length=1, max_length=300)
