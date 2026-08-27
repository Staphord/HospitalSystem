"""The deterministic medication check.

Same inputs, same ruleset, same result, every time. There is no model call here
and no module in this file's import graph can reach one.

The engine is built around a single rule: it may only conclude when an approved,
current ruleset actually answered. Everything else — no ruleset, a stale one, a
failed load, a medicine it could not identify, a medicine the clinician has not
confirmed, a missing dose or route or form, an allergy history that was never
recorded — produces needs_review. None of those are rendered as "no interaction
found", because none of them are.

Two checks deliberately never produce a decided alert even when a ruleset is
loaded: duplicate ingredient and duplicate therapy. The engine can prove two
products share an ingredient, which is an identity fact, but whether that
duplication is wrong for this patient is a clinical judgement about dose and
intent. It reports the fact and sends it for review rather than inventing a
severity for it.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.cds.contracts import (
    AlertStatus,
    AlertType,
    CheckStatus,
    MedicationFinding,
    NormalizedMedicine,
    ResolutionState,
    ReviewReason,
    RulesetDescriptor,
    Severity,
)
from app.cds.rules import ActiveRuleset, InteractionRule, RulesetLoad
from app.cds.terminology import normalize_text

logger = logging.getLogger("cds.engine")

ALL_CHECKS: tuple[AlertType, ...] = (
    AlertType.DRUG_DRUG,
    AlertType.DRUG_ALLERGY,
    AlertType.DUPLICATE_INGREDIENT,
    AlertType.DUPLICATE_THERAPY,
    AlertType.FORMULARY_RESTRICTION,
)

_IDENTITY_LIMITATION = (
    "Identity was matched against this hospital's own inventory, which is a "
    "stock list rather than a clinical terminology service."
)
_NO_RULESET_LIMITATION = (
    "No approved interaction ruleset answered this check, so no interaction, "
    "contraindication, or formulary conclusion was reached."
)
_DUPLICATE_LIMITATION = (
    "Duplication is an identity match. Whether it is clinically appropriate for "
    "this patient is not decided here."
)


@dataclass(frozen=True)
class CheckOutcome:
    status: CheckStatus
    findings: tuple[MedicationFinding, ...]
    review_reasons: tuple[ReviewReason, ...]
    checks_performed: tuple[AlertType, ...]
    checks_not_performed: tuple[AlertType, ...]
    limitations: tuple[str, ...]
    ruleset: RulesetDescriptor | None


def _finding_id(*parts: str) -> str:
    """A stable identifier for one finding.

    Derived from the finding's own content so that an acknowledgement recorded
    against it can be matched back to the same finding on a later run, and so
    that two identical checks produce identical ids.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _label(medicine: NormalizedMedicine) -> str:
    return medicine.canonical_name or medicine.submitted_name


def _ingredient_of(medicine: NormalizedMedicine) -> str:
    return normalize_text(medicine.ingredient_key or medicine.canonical_key or "")


def _identity_findings(
    medicines: list[NormalizedMedicine], evaluated_at: datetime
) -> list[MedicationFinding]:
    """Refuse to check what the server could not pin down."""
    findings: list[MedicationFinding] = []

    for medicine in medicines:
        reasons: list[ReviewReason] = []
        if medicine.resolution is ResolutionState.UNRESOLVED:
            reasons.append(ReviewReason.UNRESOLVED_MEDICINE)
        elif medicine.resolution is ResolutionState.AMBIGUOUS:
            reasons.append(ReviewReason.AMBIGUOUS_MEDICINE)
        elif not medicine.confirmed:
            reasons.append(ReviewReason.UNCONFIRMED_MEDICINE)

        if reasons:
            findings.append(
                MedicationFinding(
                    finding_id=_finding_id("identity", medicine.submitted_name, reasons[0].value),
                    type=AlertType.UNRESOLVED_IDENTITY,
                    status=AlertStatus.NEEDS_REVIEW,
                    severity=Severity.UNKNOWN,
                    involved=[medicine],
                    explanation=(
                        f"'{medicine.submitted_name}' was not resolved to a single confirmed "
                        "product, so it was not checked."
                    ),
                    review_action=(
                        "Confirm exactly which product is intended, then run the check again."
                    ),
                    limitations=[_IDENTITY_LIMITATION],
                    review_reasons=reasons,
                    evaluated_at=evaluated_at,
                )
            )
            continue

        if medicine.missing_critical_inputs:
            findings.append(
                MedicationFinding(
                    finding_id=_finding_id(
                        "missing",
                        medicine.canonical_key or medicine.submitted_name,
                        ",".join(r.value for r in medicine.missing_critical_inputs),
                    ),
                    type=AlertType.MISSING_CRITICAL_INPUT,
                    status=AlertStatus.NEEDS_REVIEW,
                    severity=Severity.UNKNOWN,
                    involved=[medicine],
                    explanation=(
                        f"'{_label(medicine)}' is missing information the check depends on: "
                        + ", ".join(
                            r.value.replace("missing_", "") for r in medicine.missing_critical_inputs
                        )
                        + "."
                    ),
                    review_action="Record the missing details, then run the check again.",
                    limitations=[],
                    review_reasons=list(medicine.missing_critical_inputs),
                    evaluated_at=evaluated_at,
                )
            )

    return findings


def _checkable(medicines: list[NormalizedMedicine]) -> list[NormalizedMedicine]:
    """Only fully identified, confirmed medicines take part in rule matching."""
    return [
        m
        for m in medicines
        if m.resolution is ResolutionState.RESOLVED
        and m.confirmed
        and not m.missing_critical_inputs
        and _ingredient_of(m)
    ]


def _duplicate_findings(
    medicines: list[NormalizedMedicine], evaluated_at: datetime
) -> list[MedicationFinding]:
    findings: list[MedicationFinding] = []

    by_ingredient: dict[str, list[NormalizedMedicine]] = {}
    for medicine in medicines:
        by_ingredient.setdefault(_ingredient_of(medicine), []).append(medicine)

    for ingredient, group in sorted(by_ingredient.items()):
        if len(group) < 2:
            continue
        findings.append(
            MedicationFinding(
                finding_id=_finding_id("duplicate_ingredient", ingredient),
                type=AlertType.DUPLICATE_INGREDIENT,
                status=AlertStatus.NEEDS_REVIEW,
                severity=Severity.UNKNOWN,
                involved=group[:10],
                explanation=(
                    "These products share the same ingredient: "
                    + ", ".join(_label(m) for m in group)
                    + "."
                ),
                review_action=(
                    "Confirm the duplication is intended, or consolidate to one product."
                ),
                limitations=[_DUPLICATE_LIMITATION],
                review_reasons=[ReviewReason.DUPLICATION_NEEDS_CLINICAL_JUDGEMENT],
                evaluated_at=evaluated_at,
            )
        )

    by_class: dict[str, list[NormalizedMedicine]] = {}
    for medicine in medicines:
        therapeutic_class = normalize_text(medicine.therapeutic_class)
        if therapeutic_class:
            by_class.setdefault(therapeutic_class, []).append(medicine)

    for therapeutic_class, group in sorted(by_class.items()):
        distinct = {_ingredient_of(m) for m in group}
        if len(group) < 2 or len(distinct) < 2:
            # One ingredient twice is already reported above; reporting it again
            # as duplicate therapy would double-count the same fact.
            continue
        findings.append(
            MedicationFinding(
                finding_id=_finding_id("duplicate_therapy", therapeutic_class),
                type=AlertType.DUPLICATE_THERAPY,
                status=AlertStatus.NEEDS_REVIEW,
                severity=Severity.UNKNOWN,
                involved=group[:10],
                explanation=(
                    "These products are recorded under the same therapeutic category "
                    f"'{therapeutic_class}': " + ", ".join(_label(m) for m in group) + "."
                ),
                review_action="Confirm whether both are intended for this patient.",
                limitations=[
                    _DUPLICATE_LIMITATION,
                    "The category comes from this hospital's inventory records, not from a "
                    "clinical classification system.",
                ],
                review_reasons=[ReviewReason.DUPLICATION_NEEDS_CLINICAL_JUDGEMENT],
                evaluated_at=evaluated_at,
            )
        )

    return findings


def _rule_finding(
    rule: InteractionRule,
    descriptor: RulesetDescriptor,
    involved: list[NormalizedMedicine],
    explanation: str,
    evaluated_at: datetime,
) -> MedicationFinding:
    return MedicationFinding(
        finding_id=_finding_id(
            rule.rule_id,
            descriptor.ruleset_version,
            ",".join(sorted(_ingredient_of(m) for m in involved)),
        ),
        type=rule.type,
        status=AlertStatus.ALERT,
        severity=rule.severity,
        involved=involved[:10],
        explanation=explanation,
        review_action=rule.review_action,
        limitations=list(rule.limitations),
        review_reasons=[],
        rule_id=rule.rule_id,
        source_name=descriptor.source_name,
        ruleset_version=descriptor.ruleset_version,
        effective_date=descriptor.effective_date,
        evaluated_at=evaluated_at,
        blocking=rule.blocking,
    )


def _rule_findings(
    ruleset: ActiveRuleset,
    medicines: list[NormalizedMedicine],
    allergies: list[str],
    evaluated_at: datetime,
) -> list[MedicationFinding]:
    descriptor = ruleset.descriptor
    findings: list[MedicationFinding] = []

    by_ingredient: dict[str, list[NormalizedMedicine]] = {}
    for medicine in medicines:
        by_ingredient.setdefault(_ingredient_of(medicine), []).append(medicine)
    present = set(by_ingredient)

    for rule in ruleset.drug_drug_rules():
        if not rule.ingredient_keys.issubset(present):
            continue
        involved = [by_ingredient[key][0] for key in sorted(rule.ingredient_keys)]
        findings.append(
            _rule_finding(rule, descriptor, involved, rule.explanation, evaluated_at)
        )

    normalized_allergies = {normalize_text(a) for a in allergies if normalize_text(a)}
    for rule in ruleset.drug_allergy_rules():
        if rule.allergen not in normalized_allergies:
            continue
        matched = sorted(rule.ingredient_keys & present)
        if not matched:
            continue
        involved = [by_ingredient[key][0] for key in matched]
        findings.append(
            _rule_finding(rule, descriptor, involved, rule.explanation, evaluated_at)
        )

    for rule in ruleset.formulary_rules():
        matched = sorted(rule.ingredient_keys & present)
        if not matched:
            continue
        involved = [by_ingredient[key][0] for key in matched]
        findings.append(
            _rule_finding(rule, descriptor, involved, rule.explanation, evaluated_at)
        )

    return findings


def evaluate(
    medicines: list[NormalizedMedicine],
    allergies: list[str] | None,
    ruleset_load: RulesetLoad,
    evaluated_at: datetime | None = None,
) -> CheckOutcome:
    """Run every deterministic check and decide the overall status.

    `allergies` is None when the patient's allergy history has never been
    recorded, which is different from an empty list meaning "recorded, and
    there are none". The first is a hole in the data and becomes needs_review;
    the second is a fact the check can use.
    """
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    findings: list[MedicationFinding] = list(_identity_findings(medicines, evaluated_at))
    checkable = _checkable(medicines)

    performed: list[AlertType] = []
    not_performed: list[AlertType] = []
    limitations: list[str] = [_IDENTITY_LIMITATION]

    findings.extend(_duplicate_findings(checkable, evaluated_at))
    performed.extend([AlertType.DUPLICATE_INGREDIENT, AlertType.DUPLICATE_THERAPY])

    ruleset = ruleset_load.ruleset
    if not ruleset_load.usable or ruleset is None:
        reason = ruleset_load.reason or ReviewReason.NO_APPROVED_RULESET
        not_performed.extend(
            [AlertType.DRUG_DRUG, AlertType.DRUG_ALLERGY, AlertType.FORMULARY_RESTRICTION]
        )
        limitations.append(_NO_RULESET_LIMITATION)
        findings.append(
            MedicationFinding(
                finding_id=_finding_id("check_unavailable", reason.value),
                type=AlertType.CHECK_UNAVAILABLE,
                status=AlertStatus.NEEDS_REVIEW,
                severity=Severity.UNKNOWN,
                involved=checkable[:10],
                explanation=(
                    "Interaction, contraindication, and formulary checking did not run "
                    "because no approved, current ruleset was available. This is not a "
                    "statement that these medicines are safe together."
                ),
                review_action=(
                    "Refer to a pharmacist for a manual medication review before dispensing."
                ),
                limitations=[_NO_RULESET_LIMITATION],
                review_reasons=[reason],
                evaluated_at=evaluated_at,
            )
        )
    else:
        performed.extend(
            [AlertType.DRUG_DRUG, AlertType.DRUG_ALLERGY, AlertType.FORMULARY_RESTRICTION]
        )
        if allergies is None:
            not_performed.append(AlertType.DRUG_ALLERGY)
            performed.remove(AlertType.DRUG_ALLERGY)
            findings.append(
                MedicationFinding(
                    finding_id=_finding_id("missing_allergy_history"),
                    type=AlertType.DRUG_ALLERGY,
                    status=AlertStatus.NEEDS_REVIEW,
                    severity=Severity.UNKNOWN,
                    involved=[],
                    explanation=(
                        "This patient has no recorded allergy history, so contraindication "
                        "checking did not run. An empty record is not the same as no known "
                        "allergies."
                    ),
                    review_action="Take and record an allergy history, then run the check again.",
                    limitations=[],
                    review_reasons=[ReviewReason.MISSING_ALLERGY_HISTORY],
                    evaluated_at=evaluated_at,
                )
            )

        try:
            findings.extend(
                _rule_findings(ruleset, checkable, allergies or [], evaluated_at)
            )
        except Exception:
            # A rule evaluation that blew up has not cleared anything. Say so.
            logger.exception("cds rule evaluation failed")
            findings.append(
                MedicationFinding(
                    finding_id=_finding_id("check_failed", ruleset.descriptor.ruleset_version),
                    type=AlertType.CHECK_UNAVAILABLE,
                    status=AlertStatus.NEEDS_REVIEW,
                    severity=Severity.UNKNOWN,
                    involved=checkable[:10],
                    explanation=(
                        "The medication check could not be completed. No conclusion was "
                        "reached about these medicines."
                    ),
                    review_action="Refer to a pharmacist for a manual medication review.",
                    limitations=[],
                    review_reasons=[ReviewReason.CHECK_FAILED],
                    evaluated_at=evaluated_at,
                )
            )

    review_reasons: list[ReviewReason] = []
    for finding in findings:
        for reason in finding.review_reasons:
            if reason not in review_reasons:
                review_reasons.append(reason)

    if review_reasons:
        status = CheckStatus.NEEDS_REVIEW
    elif any(f.status is AlertStatus.ALERT for f in findings):
        status = CheckStatus.ALERTS
    else:
        status = CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET

    descriptor = ruleset.descriptor if ruleset is not None else None

    return CheckOutcome(
        status=status,
        findings=tuple(findings),
        review_reasons=tuple(review_reasons),
        checks_performed=tuple(dict.fromkeys(performed)),
        checks_not_performed=tuple(dict.fromkeys(not_performed)),
        limitations=tuple(dict.fromkeys(limitations)),
        ruleset=descriptor,
    )
