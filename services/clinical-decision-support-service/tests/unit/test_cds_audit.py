"""What the audit records, and what it refuses to record.

The durable record lives in the tenant database with the rest of that hospital's
clinical record. The application log gets identifiers and counts only, because
an engineer debugging a latency spike has no business reading who was
prescribed what.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.cds.audit import (
    FORBIDDEN_LOG_FIELDS,
    SAFE_LOG_FIELDS,
    build_log_record,
    log_action,
    log_check,
)
from app.cds.contracts import (
    AlertAction,
    AlertStatus,
    AlertType,
    CheckStatus,
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

PATIENT_MEDICINE = NormalizedMedicine(
    submitted_name="Warfarin 5mg",
    resolution=ResolutionState.RESOLVED,
    canonical_key="WAR-5",
    canonical_name="Warfarin",
    ingredient_key="warfarin",
    confirmed=True,
    source="test",
)

DESCRIPTOR = RulesetDescriptor(
    source_name="test-source",
    ruleset_version="1.0.0",
    effective_date=TODAY - timedelta(days=10),
    approved_by="Test Approver",
    approved_at=NOW,
    rule_count=1,
)


def response(**overrides) -> MedicationCheckResponse:
    values = {
        "request_id": "req-1",
        "check_id": uuid4(),
        "visit_id": uuid4(),
        "status": CheckStatus.ALERTS,
        "findings": [
            MedicationFinding(
                finding_id="f1",
                type=AlertType.DRUG_DRUG,
                status=AlertStatus.ALERT,
                severity=Severity.HIGH,
                involved=[PATIENT_MEDICINE],
                explanation="Warfarin and ibuprofen together increase bleeding risk.",
                review_action="Consider an alternative analgesic.",
                rule_id="R-1",
                source_name="test-source",
                ruleset_version="1.0.0",
                effective_date=TODAY - timedelta(days=10),
                evaluated_at=NOW,
                blocking=True,
            ),
            MedicationFinding(
                finding_id="f2",
                type=AlertType.DUPLICATE_INGREDIENT,
                status=AlertStatus.NEEDS_REVIEW,
                severity=Severity.UNKNOWN,
                explanation="Duplicate ingredient.",
                review_action="Confirm.",
                review_reasons=[ReviewReason.DUPLICATION_NEEDS_CLINICAL_JUDGEMENT],
                evaluated_at=NOW,
            ),
        ],
        "medicines": [PATIENT_MEDICINE],
        "review_reasons": [ReviewReason.DUPLICATION_NEEDS_CLINICAL_JUDGEMENT],
        "ruleset": DESCRIPTOR,
        "evaluated_at": NOW,
    }
    values.update(overrides)
    values["status"] = CheckStatus.NEEDS_REVIEW
    return MedicationCheckResponse(**values)


@pytest.fixture
def record():
    built = response()
    return built, build_log_record(
        request_id="req-1",
        check_id=built.check_id,
        tenant_id="hosp-c5c8388b",
        actor_sub="doctor-sub-1",
        actor_role="doctor",
        visit_id=built.visit_id,
        response=built,
    )


def test_the_log_record_carries_only_allowlisted_fields(record):
    _, built = record
    assert set(built) == set(SAFE_LOG_FIELDS)


def test_the_log_record_carries_no_clinical_content(record):
    _, built = record
    for field in FORBIDDEN_LOG_FIELDS:
        assert field not in built


def test_the_log_record_carries_no_patient_or_drug_text(record):
    _, built = record
    serialized = " ".join(str(value) for value in built.values()).lower()
    for forbidden in ("warfarin", "ibuprofen", "bleeding", "penicillin", "pt-4891"):
        assert forbidden not in serialized


def test_the_log_record_counts_what_happened(record):
    _, built = record
    assert built["finding_count"] == 2
    assert built["alert_count"] == 1
    assert built["needs_review_count"] == 1


def test_the_log_record_names_the_ruleset_so_a_result_can_be_reproduced(record):
    _, built = record
    assert built["ruleset_source"] == "test-source"
    assert built["ruleset_version"] == "1.0.0"
    assert built["ruleset_stale"] is False


def test_the_log_record_identifies_the_actor_and_tenant(record):
    _, built = record
    assert built["actor_sub"] == "doctor-sub-1"
    assert built["actor_role"] == "doctor"
    assert built["tenant_id"] == "hosp-c5c8388b"


def test_a_check_with_no_ruleset_records_that_fact(record):
    built = response(ruleset=None, findings=[], review_reasons=[ReviewReason.NO_APPROVED_RULESET])
    log = build_log_record(
        request_id="req-2",
        check_id=built.check_id,
        tenant_id="hosp-c5c8388b",
        actor_sub="doctor-sub-1",
        actor_role="doctor",
        visit_id=built.visit_id,
        response=built,
    )
    assert log["ruleset_version"] is None
    assert log["status"] == "needs_review"


def test_the_emitted_log_line_contains_no_clinical_text(record, caplog):
    _, built = record
    with caplog.at_level("INFO"):
        log_check(built)

    logged = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "check_id=" in logged
    for forbidden in ("warfarin", "ibuprofen", "bleeding"):
        assert forbidden not in logged


def test_an_override_reason_never_reaches_the_log(caplog):
    with caplog.at_level("INFO"):
        log_action(
            request_id="req-1",
            action_id=uuid4(),
            check_id=uuid4(),
            tenant_id="hosp-c5c8388b",
            actor_sub="doctor-sub-1",
            actor_role="doctor",
            action=AlertAction.OVERRIDE,
        )

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "action=override" in logged
    # The reason is clinical narrative. It belongs in the tenant database, not
    # in an application log an operations team can read.
    assert "reason" not in logged
