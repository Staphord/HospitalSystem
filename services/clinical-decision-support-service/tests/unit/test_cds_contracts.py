"""The response contract, which is where "unknown never looks safe" is enforced.

These assertions are structural rather than behavioural on purpose. Even if the
engine were rewritten badly tomorrow, it could not construct a response that
says "no alerts" without an approved, current ruleset behind it, because the
model refuses to be built.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.cds.contracts import (
    AlertAction,
    AlertActionRequest,
    AlertStatus,
    AlertType,
    CheckStatus,
    MedicationCheckRequest,
    MedicationCheckResponse,
    MedicationFinding,
    NormalizedMedicine,
    ResolutionState,
    ReviewReason,
    RulesetDescriptor,
    Severity,
)

NOW = datetime.now(timezone.utc)
TODAY = date.today()


def descriptor(**overrides) -> RulesetDescriptor:
    values = {
        "source_name": "test-source",
        "ruleset_version": "1.0.0",
        "effective_date": TODAY - timedelta(days=10),
        "review_date": TODAY + timedelta(days=100),
        "approved_by": "Test Approver",
        "approved_at": NOW,
        "rule_count": 1,
        "stale": False,
    }
    values.update(overrides)
    return RulesetDescriptor(**values)


def medicine(**overrides) -> NormalizedMedicine:
    values = {
        "submitted_name": "Warfarin 5mg",
        "resolution": ResolutionState.RESOLVED,
        "canonical_key": "WAR-5",
        "canonical_name": "Warfarin",
        "ingredient_key": "warfarin",
        "confirmed": True,
        "source": "test",
    }
    values.update(overrides)
    return NormalizedMedicine(**values)


def alert(**overrides) -> MedicationFinding:
    values = {
        "finding_id": "f1",
        "type": AlertType.DRUG_DRUG,
        "status": AlertStatus.ALERT,
        "severity": Severity.HIGH,
        "involved": [medicine()],
        "explanation": "Test explanation.",
        "review_action": "Test action.",
        "rule_id": "R-1",
        "source_name": "test-source",
        "ruleset_version": "1.0.0",
        "effective_date": TODAY - timedelta(days=10),
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return MedicationFinding(**values)


def needs_review(**overrides) -> MedicationFinding:
    values = {
        "finding_id": "f2",
        "type": AlertType.CHECK_UNAVAILABLE,
        "status": AlertStatus.NEEDS_REVIEW,
        "severity": Severity.UNKNOWN,
        "explanation": "Could not be decided.",
        "review_action": "Refer to a pharmacist.",
        "review_reasons": [ReviewReason.NO_APPROVED_RULESET],
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return MedicationFinding(**values)


def response(**overrides) -> MedicationCheckResponse:
    values = {
        "request_id": "req-1",
        "check_id": uuid4(),
        "visit_id": uuid4(),
        "status": CheckStatus.NEEDS_REVIEW,
        "findings": [needs_review()],
        "review_reasons": [ReviewReason.NO_APPROVED_RULESET],
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return MedicationCheckResponse(**values)


# Findings


def test_a_needs_review_finding_cannot_carry_a_severity():
    with pytest.raises(ValidationError, match="cannot carry a severity"):
        needs_review(severity=Severity.HIGH)


def test_a_needs_review_finding_must_say_why():
    with pytest.raises(ValidationError, match="must say why"):
        needs_review(review_reasons=[])


def test_a_needs_review_finding_cannot_be_blocking():
    with pytest.raises(ValidationError, match="not a blocking alert"):
        needs_review(blocking=True)


def test_a_decided_alert_needs_a_severity():
    with pytest.raises(ValidationError, match="must carry a severity"):
        alert(severity=Severity.UNKNOWN)


@pytest.mark.parametrize(
    "field", ["rule_id", "source_name", "ruleset_version", "effective_date"]
)
def test_a_decided_alert_must_cite_its_provenance(field):
    with pytest.raises(ValidationError, match="must cite its ruleset provenance"):
        alert(**{field: None})


# Whole-check status


def test_needs_review_must_say_why():
    with pytest.raises(ValidationError, match="needs_review must say why"):
        response(review_reasons=[])


def test_no_alerts_requires_a_ruleset_to_have_answered():
    # The heart of it: without an approved ruleset there is no such thing as
    # "no interaction found", so the response cannot be constructed.
    with pytest.raises(ValidationError, match="only a check backed by an approved ruleset"):
        response(
            status=CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET,
            findings=[],
            review_reasons=[],
            ruleset=None,
        )


def test_a_stale_ruleset_cannot_conclude():
    with pytest.raises(ValidationError, match="stale ruleset cannot conclude"):
        response(
            status=CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET,
            findings=[],
            review_reasons=[],
            ruleset=descriptor(stale=True),
        )


def test_no_alerts_cannot_hide_an_unresolved_finding():
    with pytest.raises(ValidationError, match="makes the whole check needs_review"):
        response(
            status=CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET,
            findings=[needs_review()],
            review_reasons=[],
            ruleset=descriptor(),
        )


def test_no_alerts_cannot_hide_an_alert():
    with pytest.raises(ValidationError, match="cannot carry an alert"):
        response(
            status=CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET,
            findings=[alert()],
            review_reasons=[],
            ruleset=descriptor(),
        )


def test_alerts_status_requires_an_alert():
    with pytest.raises(ValidationError, match="requires at least one alert"):
        response(
            status=CheckStatus.ALERTS,
            findings=[],
            review_reasons=[],
            ruleset=descriptor(),
        )


def test_a_backed_check_with_nothing_found_is_valid():
    built = response(
        status=CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET,
        findings=[],
        review_reasons=[],
        ruleset=descriptor(),
    )
    assert built.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET
    assert built.requires_human_review is True


def test_human_review_cannot_be_switched_off():
    with pytest.raises(ValidationError, match="cannot be disabled"):
        response(requires_human_review=False)


def test_the_vocabulary_has_no_word_for_safe():
    values = {status.value for status in CheckStatus}
    assert values == {"alerts", "no_alerts_in_active_ruleset", "needs_review"}
    for forbidden in ("safe", "clear", "ok", "none", "no_interaction_found", "pass"):
        assert forbidden not in values


# Client boundary


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "roles",
        "database_url",
        "api_key",
        "severity",
        "ruleset_version",
        "rule_id",
        "status",
        "actor_sub",
        "sql",
    ],
)
def test_server_authoritative_fields_are_refused_from_the_client(field):
    with pytest.raises(ValidationError, match="FORBIDDEN_CLIENT_FIELD"):
        MedicationCheckRequest(**{"visit_id": str(uuid4()), field: "anything"})


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        MedicationCheckRequest(visit_id=str(uuid4()), surprise="value")


def test_an_override_without_a_reason_is_refused():
    with pytest.raises(ValidationError, match="requires a reason"):
        AlertActionRequest(check_id=uuid4(), finding_id="f1", action=AlertAction.OVERRIDE)

    with pytest.raises(ValidationError, match="requires a reason"):
        AlertActionRequest(
            check_id=uuid4(), finding_id="f1", action=AlertAction.OVERRIDE, reason="   "
        )


def test_an_acknowledgement_needs_no_reason():
    built = AlertActionRequest(
        check_id=uuid4(), finding_id="f1", action=AlertAction.ACKNOWLEDGE
    )
    assert built.reason is None


def test_the_contract_carries_no_prescription_or_treatment_field():
    # The response must not be able to express an instruction. Anything that
    # changes a medication order stays in the consultation and pharmacy
    # workflows, behind their own authorization.
    fields = set(MedicationCheckResponse.model_fields)
    for forbidden in (
        "prescription",
        "prescription_id",
        "dose_change",
        "new_dose",
        "substitute",
        "discontinue",
        "order",
        "treatment",
        "referral",
    ):
        assert forbidden not in fields
