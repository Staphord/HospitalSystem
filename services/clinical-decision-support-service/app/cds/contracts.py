from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Fields that are server-authoritative or secret and must never be accepted from
# a browser, a prompt, a transcript, or a model response. Tenant identity, role,
# database routing, and the ruleset a result was evaluated against are all
# resolved on the server. A client able to name its own ruleset version could
# make an alert look as though an approved source had produced it.
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
        # Result provenance is decided by the engine, never asserted by a caller.
        "severity",
        "rule_id",
        "ruleset",
        "ruleset_version",
        "ruleset_source",
        "source_name",
        "effective_date",
        "evaluated_at",
        "status",
    }
)


class StrictModel(BaseModel):
    """Base for every CDS model. Unknown fields are rejected outright."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CdsClientModel(StrictModel):
    """Base for contracts crossing the client boundary.

    Server-built records that legitimately carry resolved tenant identity, a
    severity, or a ruleset version extend StrictModel instead.
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


class CheckStatus(str, Enum):
    """The outcome of a whole medication check.

    There is deliberately no value meaning "safe" or "clear". The closest this
    vocabulary comes is NO_ALERTS_IN_ACTIVE_RULESET, which is a statement about
    one named, versioned ruleset and nothing more, and which the response
    contract refuses to carry unless an approved ruleset actually answered.
    """

    ALERTS = "alerts"
    NO_ALERTS_IN_ACTIVE_RULESET = "no_alerts_in_active_ruleset"
    NEEDS_REVIEW = "needs_review"


class AlertStatus(str, Enum):
    """Whether a finding is a decided alert or an unresolved question."""

    ALERT = "alert"
    NEEDS_REVIEW = "needs_review"


class Severity(str, Enum):
    """Severity is set by the approved ruleset, never inferred and never guessed.

    UNKNOWN is where every finding starts and the only value a needs_review
    finding may hold.
    """

    UNKNOWN = "unknown"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(str, Enum):
    DRUG_DRUG = "drug_drug"
    DRUG_ALLERGY = "drug_allergy"
    DUPLICATE_INGREDIENT = "duplicate_ingredient"
    DUPLICATE_THERAPY = "duplicate_therapy"
    FORMULARY_RESTRICTION = "formulary_restriction"
    MISSING_CRITICAL_INPUT = "missing_critical_input"
    UNRESOLVED_IDENTITY = "unresolved_identity"
    CHECK_UNAVAILABLE = "check_unavailable"


class ReviewReason(str, Enum):
    """Why a check could not conclude. Every one of these means needs_review."""

    NO_APPROVED_RULESET = "no_approved_ruleset"
    RULESET_STALE = "ruleset_stale"
    RULESET_LOAD_FAILED = "ruleset_load_failed"
    CHECK_FAILED = "check_failed"
    UNRESOLVED_MEDICINE = "unresolved_medicine"
    AMBIGUOUS_MEDICINE = "ambiguous_medicine"
    UNCONFIRMED_MEDICINE = "unconfirmed_medicine"
    MISSING_DOSE = "missing_dose"
    MISSING_ROUTE = "missing_route"
    MISSING_FORM = "missing_form"
    MISSING_ALLERGY_HISTORY = "missing_allergy_history"
    # Duplication is proven by identity, but whether it is appropriate for this
    # patient is a dosing judgement no ruleset in scope decides, so it is always
    # referred rather than given a severity.
    DUPLICATION_NEEDS_CLINICAL_JUDGEMENT = "duplication_needs_clinical_judgement"


class ResolutionState(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class AlertAction(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    OVERRIDE = "override"


class CdsErrorCode(str, Enum):
    CAPABILITY_DISABLED = "capability_disabled"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    RESOURCE_NOT_FOUND = "resource_not_found"
    TOO_MANY_MEDICINES = "too_many_medicines"
    CHECK_UNAVAILABLE = "check_unavailable"


# Normalization


class MedicineInput(CdsClientModel):
    """One medicine as the clinician typed or selected it.

    confirmed_key is the canonical key the clinician confirmed after seeing the
    normalization result. It is a lookup key into this tenant's own formulary,
    not a severity, a rule, or anything the engine treats as clinical fact.
    """

    display_name: str = Field(min_length=1, max_length=200)
    dose: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=50)
    form: str | None = Field(default=None, max_length=50)
    confirmed_key: str | None = Field(default=None, max_length=200)


class NormalizedMedicine(StrictModel):
    """A medicine after normalization, as the engine sees it."""

    submitted_name: str = Field(max_length=200)
    resolution: ResolutionState
    canonical_key: str | None = Field(default=None, max_length=200)
    canonical_name: str | None = Field(default=None, max_length=200)
    ingredient_key: str | None = Field(default=None, max_length=200)
    therapeutic_class: str | None = Field(default=None, max_length=100)
    strength: str | None = Field(default=None, max_length=50)
    form: str | None = Field(default=None, max_length=50)
    route: str | None = Field(default=None, max_length=50)
    dose: str | None = Field(default=None, max_length=100)
    confirmed: bool = False
    source: str = Field(max_length=100)
    missing_critical_inputs: list[ReviewReason] = Field(default_factory=list, max_length=10)


class NormalizationCandidate(StrictModel):
    """One possible match offered back for the clinician to confirm."""

    canonical_key: str = Field(max_length=200)
    canonical_name: str = Field(max_length=200)
    ingredient_key: str | None = Field(default=None, max_length=200)
    therapeutic_class: str | None = Field(default=None, max_length=100)
    strength: str | None = Field(default=None, max_length=50)
    form: str | None = Field(default=None, max_length=50)


class NormalizeRequest(CdsClientModel):
    """Ask what typed medicine names resolve to, before any check runs."""

    medicines: list[MedicineInput] = Field(min_length=1, max_length=30)


class NormalizeResult(StrictModel):
    submitted_name: str = Field(max_length=200)
    resolution: ResolutionState
    candidates: list[NormalizationCandidate] = Field(default_factory=list, max_length=10)
    requires_confirmation: bool = True
    missing_critical_inputs: list[ReviewReason] = Field(default_factory=list, max_length=10)


class NormalizeResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    source: str = Field(max_length=100)
    results: list[NormalizeResult] = Field(default_factory=list, max_length=30)
    evaluated_at: datetime


# Ruleset provenance


class RulesetDescriptor(StrictModel):
    """Everything needed to reproduce a result later.

    A result citing a descriptor can be re-derived: same source, same version,
    same effective date, same rule.
    """

    source_name: str = Field(min_length=1, max_length=120)
    ruleset_version: str = Field(min_length=1, max_length=64)
    effective_date: date
    review_date: date | None = None
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: datetime
    rule_count: int = Field(ge=0)
    stale: bool = False


class ActiveRulesetResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    available: bool
    descriptor: RulesetDescriptor | None = None
    unavailable_reason: ReviewReason | None = None

    @model_validator(mode="after")
    def _available_means_described(self) -> "ActiveRulesetResponse":
        if self.available and self.descriptor is None:
            raise ValueError("an available ruleset must carry its descriptor")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("an unavailable ruleset must say why")
        return self


# Findings


class MedicationFinding(StrictModel):
    """One alert, or one thing that could not be decided.

    Every field a clinician needs in order to trust, reproduce, or dispute the
    finding is present: what went in, which rule fired, from which version of
    which source, when it was evaluated, what is not known, and what to do.
    """

    finding_id: str = Field(min_length=1, max_length=64)
    type: AlertType
    status: AlertStatus
    severity: Severity
    involved: list[NormalizedMedicine] = Field(default_factory=list, max_length=10)
    explanation: str = Field(min_length=1, max_length=2000)
    review_action: str = Field(min_length=1, max_length=500)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    review_reasons: list[ReviewReason] = Field(default_factory=list, max_length=10)
    rule_id: str | None = Field(default=None, max_length=120)
    source_name: str | None = Field(default=None, max_length=120)
    ruleset_version: str | None = Field(default=None, max_length=64)
    effective_date: date | None = None
    evaluated_at: datetime
    blocking: bool = False

    @model_validator(mode="after")
    def _provenance_and_severity_are_consistent(self) -> "MedicationFinding":
        if self.status is AlertStatus.NEEDS_REVIEW:
            # An unresolved question has no severity to report and must say why.
            if self.severity is not Severity.UNKNOWN:
                raise ValueError("a needs_review finding cannot carry a severity")
            if not self.review_reasons:
                raise ValueError("a needs_review finding must say why it needs review")
            if self.blocking:
                raise ValueError("a needs_review finding is not a blocking alert")
            return self

        # A decided alert must be attributable, or it is not evidence.
        if self.severity is Severity.UNKNOWN:
            raise ValueError("a decided alert must carry a severity from its ruleset")
        missing = [
            name
            for name, value in (
                ("rule_id", self.rule_id),
                ("source_name", self.source_name),
                ("ruleset_version", self.ruleset_version),
                ("effective_date", self.effective_date),
            )
            if value in (None, "")
        ]
        if missing:
            raise ValueError(
                "a decided alert must cite its ruleset provenance, missing: " + ", ".join(missing)
            )
        return self


# Medication check


class MedicationCheckRequest(CdsClientModel):
    """Run the deterministic checks for one visit.

    The patient is not named by the client. It is resolved from the visit, and
    the caller's access to that visit is verified server-side against the tenant
    the token belongs to.
    """

    visit_id: UUID
    additional_medicines: list[MedicineInput] = Field(default_factory=list, max_length=30)
    include_prescribed: bool = True


class MedicationCheckResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    check_id: UUID
    visit_id: UUID
    status: CheckStatus
    findings: list[MedicationFinding] = Field(default_factory=list, max_length=100)
    medicines: list[NormalizedMedicine] = Field(default_factory=list, max_length=60)
    review_reasons: list[ReviewReason] = Field(default_factory=list, max_length=20)
    ruleset: RulesetDescriptor | None = None
    checks_performed: list[AlertType] = Field(default_factory=list, max_length=10)
    checks_not_performed: list[AlertType] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    requires_human_review: bool = True
    evaluated_at: datetime

    @model_validator(mode="after")
    def _status_is_earned(self) -> "MedicationCheckResponse":
        if self.status is CheckStatus.NEEDS_REVIEW:
            if not self.review_reasons:
                raise ValueError("needs_review must say why")
            return self

        # Anything other than needs_review is a claim about a specific ruleset,
        # so that ruleset has to exist and has to be current. This is what stops
        # a missing, stale, or failed check from ever reading as reassurance.
        if self.ruleset is None:
            raise ValueError("only a check backed by an approved ruleset may conclude")
        if self.ruleset.stale:
            raise ValueError("a stale ruleset cannot conclude; it needs review")

        if self.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET:
            if any(f.status is AlertStatus.ALERT for f in self.findings):
                raise ValueError("no_alerts_in_active_ruleset cannot carry an alert")
            if any(f.status is AlertStatus.NEEDS_REVIEW for f in self.findings):
                raise ValueError("an unresolved finding makes the whole check needs_review")
        if self.status is CheckStatus.ALERTS and not any(
            f.status is AlertStatus.ALERT for f in self.findings
        ):
            raise ValueError("status alerts requires at least one alert")
        return self

    @model_validator(mode="after")
    def _human_review_cannot_be_switched_off(self) -> "MedicationCheckResponse":
        if not self.requires_human_review:
            raise ValueError("clinical review is mandatory and cannot be disabled")
        return self


# Acknowledgement and override


class AlertActionRequest(CdsClientModel):
    """Record that a clinician saw a finding, or overrode a blocking one.

    An override needs a reason. The actor is taken from the token, never from
    this body, so an acknowledgement cannot be attributed to somebody else.
    """

    check_id: UUID
    finding_id: str = Field(min_length=1, max_length=64)
    action: AlertAction
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _override_needs_a_reason(self) -> "AlertActionRequest":
        if self.action is AlertAction.OVERRIDE:
            if not self.reason or not self.reason.strip():
                raise ValueError("an override requires a reason")
        return self


class AlertActionResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    action_id: UUID
    check_id: UUID
    finding_id: str = Field(max_length=64)
    action: AlertAction
    recorded_at: datetime


# Errors


class CdsErrorResponse(StrictModel):
    """A safe error. Carries no stack trace, no database error, and no PHI."""

    request_id: str = Field(min_length=1, max_length=64)
    code: CdsErrorCode
    message: str = Field(min_length=1, max_length=400)
