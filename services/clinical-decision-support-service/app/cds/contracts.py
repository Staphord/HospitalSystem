from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Fields that are server-authoritative or secret and must never be accepted from
# a browser, a prompt, a transcript, or a model response. Tenant identity, role,
# database routing, and the versions a result was produced by are all resolved on
# the server. A client able to name its own prompt version, or to supply its own
# red flags, could make a result look as though an approved source had produced
# it.
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
        "actor_role",
        "scope",
        "scopes",
        "permissions",
        "is_super_admin",
        "user_sub",
        "actor_sub",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret_key",
        "provider",
        "provider_key",
        "system_prompt",
        "prompt",
        "tools",
        "tool_names",
        "sql",
        "query",
        "url",
        "endpoint",
        # Result provenance is decided by the server, never asserted by a caller.
        # A caller able to supply its own red flags or considerations could put
        # words into a clinical result that no rule and no reviewed prompt
        # produced.
        "considerations",
        "red_flags",
        "redflags",
        "confidence",
        "probability",
        "likelihood",
        "risk_score",
        "score",
        "diagnosis",
        "model_version",
        "prompt_version",
        "knowledge_version",
        "requires_human_review",
        "rule_id",
        "ruleset_version",
        "redflag_ruleset_version",
        "evaluated_at",
        "status",
    }
)


class StrictModel(BaseModel):
    """Base for every CDS model. Unknown fields are rejected outright."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CdsClientModel(StrictModel):
    """Base for contracts crossing the client boundary.

    Server-built records that legitimately carry resolved tenant identity or a
    rule-pack version extend StrictModel instead.
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


# Vocabulary


class CdsErrorCode(str, Enum):
    CAPABILITY_DISABLED = "capability_disabled"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    RESOURCE_NOT_FOUND = "resource_not_found"
    SUGGESTION_UNAVAILABLE = "suggestion_unavailable"


# Clinical differential support


class DifferentialStatus(str, Enum):
    """The outcome of one differential request.

    There is deliberately no value meaning "diagnosed" or "concluded". The most
    this workflow ever produces is considerations for a clinician to review.
    """

    SUGGESTIONS = "suggestions"
    INSUFFICIENT_INPUT = "insufficient_input"
    UNAVAILABLE = "unavailable"


class Progression(str, Enum):
    IMPROVING = "improving"
    UNCHANGED = "unchanged"
    WORSENING = "worsening"
    FLUCTUATING = "fluctuating"
    UNKNOWN = "unknown"


class ReportedSeverity(str, Enum):
    """Severity as the patient reported it.

    A recorded observation, not a graded clinical judgement, and it never
    becomes one.
    """

    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    UNKNOWN = "unknown"


# Language that must never appear in a differential result. The model organizes
# and explains; it does not treat. A phrase list is a blunt instrument, but it
# is a deterministic one, and it fails closed: a result containing any of these
# is rejected outright rather than shown with the offending sentence removed,
# because a suggestion that had to be edited to be safe was not safe.
_DIRECTIVE_PATTERNS: tuple[str, ...] = (
    r"\bprescrib\w*",
    r"\badminister\w*",
    r"\bdispens\w*",
    r"\btitrat\w*",
    r"\bdiscontinu\w*",
    r"\bstart\s+(?:the\s+)?(?:patient\s+)?on\b",
    r"\bcommence\s+(?:the\s+)?(?:patient\s+)?on\b",
    r"\bincrease\s+the\s+dose\b",
    r"\bdecrease\s+the\s+dose\b",
    r"\brefer\s+(?:the\s+)?patient\s+to\b",
    r"\badmit\s+(?:the\s+)?patient\b",
    r"\bdischarge\s+(?:the\s+)?patient\b",
    # Any dosage at all. A differential result has no business carrying one.
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|iu|units?)\b",
    # Unsupported numeric certainty: percentages, "x% likely", odds.
    r"\b\d+(?:\.\d+)?\s*%",
    r"\bprobability\s+of\b",
    r"\blikelihood\s+of\s+\d",
)

_DIRECTIVE_RE = re.compile("|".join(_DIRECTIVE_PATTERNS), re.IGNORECASE)


def refuse_directive_language(value: str, field_name: str) -> str:
    """Reject text that treats, doses, refers, admits, or asserts a number.

    Applied to every free-text field of a differential result, whichever side
    produced it. The model is the likeliest source, but a rule pack edited badly
    would be caught by exactly the same check.
    """
    match = _DIRECTIVE_RE.search(value or "")
    if match:
        raise ValueError(
            f"{field_name} contains directive or unsupported-certainty language: "
            f"{match.group(0)!r}"
        )
    return value


class ObservedValue(StrictModel):
    """One piece of retrieved context, with when it was recorded.

    The timestamp is not decoration. A clinician needs to know that the vitals
    the suggestion used are six hours old, and a suggestion that cannot say how
    fresh its inputs were is not reviewable.
    """

    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=300)
    recorded_at: datetime | None = None
    source: str = Field(max_length=100)


class SymptomInput(CdsClientModel):
    """One structured symptom as the clinician recorded it."""

    name: str = Field(min_length=1, max_length=120)
    onset: str | None = Field(default=None, max_length=100)
    duration: str | None = Field(default=None, max_length=100)
    reported_severity: ReportedSeverity = ReportedSeverity.UNKNOWN
    location: str | None = Field(default=None, max_length=120)
    progression: Progression = Progression.UNKNOWN


class DifferentialRequest(CdsClientModel):
    """One clinician-initiated differential request for one visit.

    The department is named by the caller and checked against the approved one,
    so switching this capability on cannot silently widen it to a workflow no
    clinical owner reviewed. Patient, tenant, and every retrieved value come
    from the server.
    """

    visit_id: UUID
    chief_complaint: str = Field(min_length=1, max_length=500)
    symptoms: list[SymptomInput] = Field(default_factory=list, max_length=20)
    department: str = Field(min_length=1, max_length=60)
    encounter_type: str | None = Field(default=None, max_length=60)
    # Free text is allowed as a capture mechanism, but it is normalized into the
    # reviewable object below and is never passed through as an instruction.
    additional_notes: str | None = Field(default=None, max_length=2000)


class DifferentialInputs(StrictModel):
    """Exactly what the suggestion was built from, and how fresh it was.

    This is the reviewability contract. A clinician can read this and see the
    complete set of inputs, so nothing influenced the result that they cannot
    see here.
    """

    chief_complaint: str = Field(max_length=500)
    symptoms: list[SymptomInput] = Field(default_factory=list, max_length=20)
    department: str = Field(max_length=60)
    encounter_type: str | None = Field(default=None, max_length=60)
    vitals: list[ObservedValue] = Field(default_factory=list, max_length=20)
    patient_factors: list[ObservedValue] = Field(default_factory=list, max_length=20)
    allergies: list[str] = Field(default_factory=list, max_length=30)
    allergy_history_recorded: bool = False
    current_medicines: list[str] = Field(default_factory=list, max_length=60)
    notes_used: str | None = Field(default=None, max_length=2000)
    context_retrieved_at: datetime


class RedFlag(StrictModel):
    """A red flag from the deterministic rule pack.

    A model never produces one of these. The rule id and version are required,
    so a flag that cannot be traced to an approved rule cannot exist.
    """

    rule_id: str = Field(min_length=1, max_length=120)
    ruleset_version: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=600)
    matched_on: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _states_a_finding_not_an_order(self) -> "RedFlag":
        refuse_directive_language(self.label, "red flag label")
        refuse_directive_language(self.detail, "red flag detail")
        return self


class Consideration(StrictModel):
    """One thing worth considering, with what supports and contradicts it.

    Carries no probability, no score, and no ranking number. An unsupported
    figure would be read as precision the workflow does not have.
    """

    label: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1000)
    supporting_findings: list[str] = Field(default_factory=list, max_length=10)
    contradicting_findings: list[str] = Field(default_factory=list, max_length=10)
    evidence_references: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _organizes_rather_than_treats(self) -> "Consideration":
        refuse_directive_language(self.label, "consideration label")
        refuse_directive_language(self.rationale, "consideration rationale")
        for item in self.supporting_findings:
            refuse_directive_language(item, "supporting finding")
        for item in self.contradicting_findings:
            refuse_directive_language(item, "contradicting finding")
        return self


class DifferentialResponse(StrictModel):
    """Clinical Differential Support: considerations for clinician review.

    Never a diagnosis, never a plan, never an instruction. Every field a
    clinician needs to judge the result is present: what went in, how fresh it
    was, what is missing, what conflicts, what the deterministic rules flagged,
    what the limits are, and which model, prompt, and rule versions produced it.
    """

    request_id: str = Field(min_length=1, max_length=64)
    suggestion_id: UUID
    visit_id: UUID
    status: DifferentialStatus
    inputs: DifferentialInputs
    considerations: list[Consideration] = Field(default_factory=list, max_length=8)
    red_flags: list[RedFlag] = Field(default_factory=list, max_length=10)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    contradictions: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    evidence_references: list[str] = Field(default_factory=list, max_length=20)
    department: str = Field(max_length=60)
    knowledge_version: str = Field(max_length=64)
    redflag_ruleset_version: str = Field(max_length=64)
    prompt_version: str = Field(max_length=64)
    model_version: str | None = Field(default=None, max_length=120)
    requires_human_review: bool = True
    evaluated_at: datetime

    @model_validator(mode="after")
    def _never_concludes_and_never_treats(self) -> "DifferentialResponse":
        if not self.requires_human_review:
            # There is no path on which this workflow decides anything alone.
            raise ValueError("a differential result always requires human review")
        if self.status is not DifferentialStatus.SUGGESTIONS and self.considerations:
            raise ValueError("only a suggestions result may carry considerations")
        for item in self.missing_information + self.contradictions + self.limitations:
            refuse_directive_language(item, "differential narrative")
        return self


class DifferentialFeedbackRequest(CdsClientModel):
    """A clinician's judgement of one suggestion.

    Recorded for human review. It is deliberately not wired to anything that
    changes future output: an unreviewed learning loop would let one clinician's
    click quietly alter what the next clinician is shown.
    """

    suggestion_id: UUID
    rating: str = Field(min_length=1, max_length=20)
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _rating_is_one_of_the_known_values(self) -> "DifferentialFeedbackRequest":
        if self.rating not in {"useful", "not_useful", "incorrect", "unsafe"}:
            raise ValueError("unknown rating")
        return self


class DifferentialFeedbackResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    suggestion_id: UUID
    recorded_at: datetime


# Errors


class CdsErrorResponse(StrictModel):
    """A safe error. Carries no stack trace, no database error, and no PHI."""

    request_id: str = Field(min_length=1, max_length=64)
    code: CdsErrorCode
    message: str = Field(min_length=1, max_length=400)
