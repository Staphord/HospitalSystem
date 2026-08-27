"""The interaction-rules adapter.

Whether two medicines interact, and how badly, is a clinical judgement. It has
to come from a licensed or hospital-approved source that a named pharmacist has
signed off, carries a version, and can be re-checked later. Nothing in this
repository is such a source, and nothing in this module invents one.

So the default source is NullRulesetSource: it answers "unavailable" to every
question, and the engine turns that into needs_review. A hospital supplies a
real ruleset as a versioned artifact and points CDS_RULESET_PATH at it;
FileRulesetSource validates its metadata, its approval block, and its dates
before a single rule is allowed to fire.

Two guarantees are enforced here rather than left to convention:

* An artifact that is not approved for the running environment does not load.
  That is what keeps a test fixture out of production.
* An artifact past its review date loads as stale, and stale never concludes.
  A ruleset nobody has re-reviewed in six months is a reason to ask a
  pharmacist, not a reason to reassure a prescriber.

No language model is imported, referenced, or reachable from this module or
anything it calls.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from app.cds.contracts import AlertType, ReviewReason, RulesetDescriptor, Severity
from app.core.config import settings

logger = logging.getLogger("cds.rules")

# Rule types an artifact may declare. A rule of any other type is rejected at
# load time rather than silently ignored, because an unknown type means the
# artifact and this code disagree about what is being checked.
_RULE_TYPES: dict[str, AlertType] = {
    "drug_drug": AlertType.DRUG_DRUG,
    "drug_allergy": AlertType.DRUG_ALLERGY,
    "duplicate_therapy": AlertType.DUPLICATE_THERAPY,
    "formulary_restriction": AlertType.FORMULARY_RESTRICTION,
}

_SEVERITIES: dict[str, Severity] = {
    "low": Severity.LOW,
    "moderate": Severity.MODERATE,
    "high": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


class RulesetError(Exception):
    """An artifact could not be loaded. Never surfaced verbatim to a caller."""


@dataclass(frozen=True)
class InteractionRule:
    """One approved rule, exactly as the artifact stated it."""

    rule_id: str
    type: AlertType
    severity: Severity
    ingredient_keys: frozenset[str]
    explanation: str
    review_action: str
    allergen: str | None = None
    blocking: bool = False
    limitations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ActiveRuleset:
    descriptor: RulesetDescriptor
    rules: tuple[InteractionRule, ...]

    def drug_drug_rules(self) -> tuple[InteractionRule, ...]:
        return tuple(r for r in self.rules if r.type is AlertType.DRUG_DRUG)

    def drug_allergy_rules(self) -> tuple[InteractionRule, ...]:
        return tuple(r for r in self.rules if r.type is AlertType.DRUG_ALLERGY)

    def duplicate_therapy_rules(self) -> tuple[InteractionRule, ...]:
        return tuple(r for r in self.rules if r.type is AlertType.DUPLICATE_THERAPY)

    def formulary_rules(self) -> tuple[InteractionRule, ...]:
        return tuple(r for r in self.rules if r.type is AlertType.FORMULARY_RESTRICTION)


@dataclass(frozen=True)
class RulesetLoad:
    """What the engine gets back when it asks for today's rules.

    A load can be unavailable (no ruleset at all), stale (a ruleset exists but
    is past review), or usable. Only the third may produce a decided alert.
    """

    ruleset: ActiveRuleset | None = None
    reason: ReviewReason | None = None

    @property
    def usable(self) -> bool:
        return self.ruleset is not None and self.reason is None


class RulesetSource(Protocol):
    """The seam a licensed source, a rules engine, or a file has to satisfy."""

    name: str

    def load(self) -> RulesetLoad: ...


class NullRulesetSource:
    """No approved interaction source is configured.

    This is the default, and it is a working, correct implementation rather
    than a placeholder: with no approved source, the honest answer to "do these
    interact?" is "nobody here can tell you", and the honest handling of that
    answer is to send it to a pharmacist. It never returns zero alerts, because
    zero alerts would be a claim it has no standing to make.
    """

    name = "null"

    def load(self) -> RulesetLoad:
        return RulesetLoad(ruleset=None, reason=ReviewReason.NO_APPROVED_RULESET)


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload or payload[key] in (None, ""):
        raise RulesetError(f"ruleset artifact is missing {key}")
    return payload[key]


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RulesetError(f"ruleset artifact has an unreadable {field_name}") from exc


def _parse_datetime(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RulesetError(f"ruleset artifact has an unreadable {field_name}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_rule(raw: Any, index: int) -> InteractionRule:
    if not isinstance(raw, dict):
        raise RulesetError(f"rule {index} is not an object")

    rule_id = str(_require(raw, "rule_id"))

    raw_type = str(_require(raw, "type")).strip().lower()
    if raw_type not in _RULE_TYPES:
        raise RulesetError(f"rule {rule_id} declares an unsupported type")

    raw_severity = str(_require(raw, "severity")).strip().lower()
    if raw_severity not in _SEVERITIES:
        # "unknown" is not a severity a ruleset may assert. A rule that fires
        # has to say how bad it is, or it is not a rule.
        raise RulesetError(f"rule {rule_id} declares an unsupported severity")

    ingredients = raw.get("ingredient_keys") or raw.get("ingredients") or []
    if not isinstance(ingredients, list) or not ingredients:
        raise RulesetError(f"rule {rule_id} names no ingredients")
    ingredient_keys = frozenset(str(i).strip().lower() for i in ingredients if str(i).strip())
    if not ingredient_keys:
        raise RulesetError(f"rule {rule_id} names no usable ingredients")

    rule_type = _RULE_TYPES[raw_type]
    allergen = raw.get("allergen")
    if rule_type is AlertType.DRUG_ALLERGY and not allergen:
        raise RulesetError(f"rule {rule_id} is a drug-allergy rule with no allergen")
    if rule_type is AlertType.DRUG_DRUG and len(ingredient_keys) < 2:
        raise RulesetError(f"rule {rule_id} is a drug-drug rule naming one ingredient")

    limitations = raw.get("limitations") or []
    if not isinstance(limitations, list):
        raise RulesetError(f"rule {rule_id} has unreadable limitations")

    return InteractionRule(
        rule_id=rule_id,
        type=rule_type,
        severity=_SEVERITIES[raw_severity],
        ingredient_keys=ingredient_keys,
        explanation=str(_require(raw, "explanation")),
        review_action=str(_require(raw, "review_action")),
        allergen=str(allergen).strip().lower() if allergen else None,
        blocking=bool(raw.get("blocking", False)),
        limitations=tuple(str(item) for item in limitations),
    )


class FileRulesetSource:
    """Loads an approved ruleset artifact from disk.

    The artifact is data, not code, and it is validated as untrusted input:
    every required field is checked, every rule is parsed, and one bad rule
    fails the whole load rather than being skipped. A partially loaded safety
    ruleset is more dangerous than none, because the rules that silently
    vanished are the ones nobody would notice missing.
    """

    name = "file"

    def __init__(self, path: str, environment: str, stale_after_days: int) -> None:
        self._path = Path(path)
        self._environment = (environment or "").strip().lower()
        self._stale_after_days = max(int(stale_after_days), 0)

    def load(self) -> RulesetLoad:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.error("cds ruleset artifact not found at the configured path")
            return RulesetLoad(reason=ReviewReason.NO_APPROVED_RULESET)
        except (OSError, json.JSONDecodeError):
            # Deliberately no exception text in the log: an artifact path or a
            # parser message is not something an ordinary log should carry.
            logger.error("cds ruleset artifact could not be read")
            return RulesetLoad(reason=ReviewReason.RULESET_LOAD_FAILED)

        try:
            ruleset = self._build(payload)
        except RulesetError as exc:
            logger.error("cds ruleset artifact rejected: %s", exc)
            return RulesetLoad(reason=ReviewReason.RULESET_LOAD_FAILED)

        if ruleset.descriptor.stale:
            return RulesetLoad(ruleset=ruleset, reason=ReviewReason.RULESET_STALE)
        return RulesetLoad(ruleset=ruleset)

    def _build(self, payload: Any) -> ActiveRuleset:
        if not isinstance(payload, dict):
            raise RulesetError("ruleset artifact is not an object")

        approval = payload.get("approval")
        if not isinstance(approval, dict):
            raise RulesetError("ruleset artifact carries no approval block")

        environments = approval.get("approved_for_environments")
        if not isinstance(environments, list) or not environments:
            raise RulesetError("ruleset artifact names no approved environments")
        approved_environments = {str(e).strip().lower() for e in environments}
        if self._environment not in approved_environments:
            raise RulesetError(
                "ruleset artifact is not approved for this environment"
            )

        effective_date = _parse_date(_require(payload, "effective_date"), "effective_date")
        today = datetime.now(timezone.utc).date()
        if effective_date > today:
            raise RulesetError("ruleset artifact is not in effect yet")

        review_date_raw = payload.get("review_date")
        review_date = _parse_date(review_date_raw, "review_date") if review_date_raw else None

        # Two independent ways to go stale, and either one is enough: the
        # artifact's own review date has passed, or it has simply been in
        # effect longer than the operator allows without a fresh review.
        stale = False
        if review_date is not None and review_date < today:
            stale = True
        if self._stale_after_days and effective_date + timedelta(days=self._stale_after_days) < today:
            stale = True

        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            raise RulesetError("ruleset artifact carries no rules list")
        rules = tuple(_parse_rule(raw, index) for index, raw in enumerate(raw_rules))

        rule_ids = [r.rule_id for r in rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise RulesetError("ruleset artifact repeats a rule id")

        descriptor = RulesetDescriptor(
            source_name=str(_require(payload, "source_name")),
            ruleset_version=str(_require(payload, "ruleset_version")),
            effective_date=effective_date,
            review_date=review_date,
            approved_by=str(_require(approval, "approved_by")),
            approved_at=_parse_datetime(_require(approval, "approved_at"), "approved_at"),
            rule_count=len(rules),
            stale=stale,
        )
        return ActiveRuleset(descriptor=descriptor, rules=rules)


def build_source() -> RulesetSource:
    """Return the configured source, failing closed on anything unexpected."""
    configured = (getattr(settings, "cds_ruleset_source", "null") or "null").strip().lower()

    if configured == "file":
        path = getattr(settings, "cds_ruleset_path", None)
        if not path:
            logger.error("cds ruleset source is file but no path is configured")
            return NullRulesetSource()
        return FileRulesetSource(
            path=path,
            environment=getattr(settings, "environment", ""),
            stale_after_days=getattr(settings, "cds_ruleset_stale_after_days", 180),
        )

    if configured != "null":
        # An unrecognised source name is a misconfiguration, and the safe
        # reading of a misconfigured safety system is that it is not there.
        logger.error("cds ruleset source is not recognised; falling back to none")

    return NullRulesetSource()


def load_active_ruleset() -> RulesetLoad:
    """Load today's rules. Called per check so a swapped artifact takes effect."""
    try:
        return build_source().load()
    except Exception:
        logger.exception("cds ruleset load failed unexpectedly")
        return RulesetLoad(reason=ReviewReason.RULESET_LOAD_FAILED)


def active_ruleset_health() -> dict[str, Any]:
    """Operational summary for /health. Carries no rule content."""
    load = load_active_ruleset()
    if load.ruleset is None:
        return {
            "available": False,
            "reason": load.reason.value if load.reason else ReviewReason.NO_APPROVED_RULESET.value,
        }
    descriptor = load.ruleset.descriptor
    return {
        "available": True,
        "usable": load.usable,
        "source_name": descriptor.source_name,
        "ruleset_version": descriptor.ruleset_version,
        "effective_date": descriptor.effective_date.isoformat(),
        "review_date": descriptor.review_date.isoformat() if descriptor.review_date else None,
        "rule_count": descriptor.rule_count,
        "stale": descriptor.stale,
        "reason": load.reason.value if load.reason else None,
    }
