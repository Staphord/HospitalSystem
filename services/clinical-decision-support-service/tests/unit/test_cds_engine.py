"""The deterministic check.

The recurring assertion here is the same one stated three different ways: a
check that did not conclude must not look like a check that concluded safely.
"""

from datetime import datetime, timezone

import pytest

from app.cds.contracts import (
    AlertStatus,
    AlertType,
    CheckStatus,
    MedicineInput,
    NormalizedMedicine,
    ResolutionState,
    ReviewReason,
    Severity,
)
from app.cds.engine import evaluate
from app.cds.rules import FileRulesetSource, NullRulesetSource
from app.cds.terminology import InventoryTerminology, TerminologyEntry
from tests.ruleset_fixtures import (
    FORMULARY_DICLOFENAC,
    PENICILLIN_ALLERGY,
    WARFARIN_IBUPROFEN,
    artifact,
    write_artifact,
)

CATALOGUE = [
    TerminologyEntry(
        "WAR-5", "Warfarin", "warfarin", "Anticoagulant", aliases=("Coumadin",)
    ),
    TerminologyEntry("IBU-400", "Ibuprofen", "ibuprofen", "NSAID"),
    TerminologyEntry("DIC-50", "Diclofenac", "diclofenac", "NSAID"),
    TerminologyEntry("AMX-500", "Amoxicillin", "amoxicillin", "Antibiotic"),
    TerminologyEntry("PCM-500", "Paracetamol", "paracetamol", "Analgesic"),
]

TERMINOLOGY = InventoryTerminology(CATALOGUE)


def med(name: str, key: str, **overrides) -> NormalizedMedicine:
    """A fully identified, confirmed medicine with no missing inputs."""
    return TERMINOLOGY.to_normalized(
        MedicineInput(
            display_name=overrides.pop("display_name", name),
            dose=overrides.pop("dose", "5mg"),
            route=overrides.pop("route", "oral"),
            form=overrides.pop("form", "tablet"),
            confirmed_key=key,
        )
    )


def loaded(tmp_path, rules):
    return FileRulesetSource(
        path=write_artifact(tmp_path, artifact(rules=rules)),
        environment="test",
        stale_after_days=180,
    ).load()


def stale(tmp_path, rules):
    return FileRulesetSource(
        path=write_artifact(
            tmp_path, artifact(rules=rules, effective_days_ago=400, review_days_ahead=-1)
        ),
        environment="test",
        stale_after_days=180,
    ).load()


NONE = NullRulesetSource().load()


# With no ruleset at all


def test_with_no_ruleset_the_whole_check_needs_review():
    outcome = evaluate([med("Warfarin", "WAR-5"), med("Ibuprofen", "IBU-400")], [], NONE)

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.NO_APPROVED_RULESET in outcome.review_reasons
    assert outcome.ruleset is None


def test_with_no_ruleset_nothing_is_reported_as_an_alert():
    outcome = evaluate([med("Warfarin", "WAR-5"), med("Ibuprofen", "IBU-400")], [], NONE)

    assert all(f.status is AlertStatus.NEEDS_REVIEW for f in outcome.findings)
    assert all(f.severity is Severity.UNKNOWN for f in outcome.findings)


def test_with_no_ruleset_the_explanation_says_it_is_not_a_safety_statement():
    outcome = evaluate([med("Warfarin", "WAR-5")], [], NONE)
    unavailable = [f for f in outcome.findings if f.type is AlertType.CHECK_UNAVAILABLE]

    assert len(unavailable) == 1
    assert "not a statement that these medicines are safe" in unavailable[0].explanation.lower()
    assert "pharmacist" in unavailable[0].review_action.lower()


def test_with_no_ruleset_the_interaction_checks_are_listed_as_not_performed():
    outcome = evaluate([med("Warfarin", "WAR-5")], [], NONE)

    assert AlertType.DRUG_DRUG in outcome.checks_not_performed
    assert AlertType.DRUG_ALLERGY in outcome.checks_not_performed
    assert AlertType.FORMULARY_RESTRICTION in outcome.checks_not_performed
    assert AlertType.DRUG_DRUG not in outcome.checks_performed


def test_a_stale_ruleset_is_treated_the_same_as_no_ruleset(tmp_path):
    outcome = evaluate(
        [med("Warfarin", "WAR-5"), med("Ibuprofen", "IBU-400")],
        [],
        stale(tmp_path, [WARFARIN_IBUPROFEN]),
    )

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.RULESET_STALE in outcome.review_reasons
    assert not any(f.status is AlertStatus.ALERT for f in outcome.findings)


# Identity gates


def test_an_unresolved_medicine_stops_the_check():
    unknown = TERMINOLOGY.to_normalized(
        MedicineInput(display_name="Mystery Pills", dose="1", route="oral", form="tablet")
    )
    outcome = evaluate([unknown], [], NONE)

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.UNRESOLVED_MEDICINE in outcome.review_reasons


def test_an_unconfirmed_medicine_is_not_checked(tmp_path):
    unconfirmed = TERMINOLOGY.to_normalized(
        MedicineInput(display_name="Warfarin", dose="5mg", route="oral", form="tablet")
    )
    outcome = evaluate(
        [unconfirmed, med("Ibuprofen", "IBU-400")], [], loaded(tmp_path, [WARFARIN_IBUPROFEN])
    )

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.UNCONFIRMED_MEDICINE in outcome.review_reasons
    # The interacting pair was never evaluated, because one half of it was not
    # confirmed. It must not silently come back clean either.
    assert not any(f.type is AlertType.DRUG_DRUG for f in outcome.findings)


@pytest.mark.parametrize(
    "missing,reason",
    [
        ("dose", ReviewReason.MISSING_DOSE),
        ("route", ReviewReason.MISSING_ROUTE),
        ("form", ReviewReason.MISSING_FORM),
    ],
)
def test_a_missing_critical_input_stops_the_check(tmp_path, missing, reason):
    incomplete = TERMINOLOGY.to_normalized(
        MedicineInput(
            **{
                "display_name": "Warfarin",
                "dose": "5mg",
                "route": "oral",
                "form": "tablet",
                "confirmed_key": "WAR-5",
                missing: None,
            }
        )
    )
    outcome = evaluate(
        [incomplete, med("Ibuprofen", "IBU-400")], [], loaded(tmp_path, [WARFARIN_IBUPROFEN])
    )

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert reason in outcome.review_reasons


# With a usable ruleset


def test_a_drug_drug_rule_fires_with_full_provenance(tmp_path):
    outcome = evaluate(
        [med("Warfarin", "WAR-5"), med("Ibuprofen", "IBU-400")],
        [],
        loaded(tmp_path, [WARFARIN_IBUPROFEN]),
    )

    assert outcome.status is CheckStatus.ALERTS
    alerts = [f for f in outcome.findings if f.status is AlertStatus.ALERT]
    assert len(alerts) == 1

    alert = alerts[0]
    assert alert.type is AlertType.DRUG_DRUG
    assert alert.severity is Severity.HIGH
    assert alert.rule_id == "TEST-DDI-0001"
    assert alert.ruleset_version == "test-1.0.0"
    assert alert.effective_date is not None
    assert alert.blocking is True
    assert {m.canonical_key for m in alert.involved} == {"WAR-5", "IBU-400"}


def test_a_rule_needing_both_medicines_does_not_fire_on_one(tmp_path):
    outcome = evaluate([med("Warfarin", "WAR-5")], [], loaded(tmp_path, [WARFARIN_IBUPROFEN]))

    assert outcome.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET
    assert outcome.findings == ()


def test_an_allergy_rule_fires_only_against_a_recorded_allergy(tmp_path):
    ruleset = loaded(tmp_path, [PENICILLIN_ALLERGY])

    hit = evaluate([med("Amoxicillin", "AMX-500")], ["penicillin"], ruleset)
    assert hit.status is CheckStatus.ALERTS
    assert hit.findings[0].severity is Severity.CRITICAL

    miss = evaluate([med("Amoxicillin", "AMX-500")], ["latex"], ruleset)
    assert miss.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET


def test_an_unrecorded_allergy_history_is_not_an_absence_of_allergies(tmp_path):
    # None means nobody ever asked. That is a hole in the data, not a clean bill
    # of health, and it must not be checked past.
    outcome = evaluate([med("Amoxicillin", "AMX-500")], None, loaded(tmp_path, [PENICILLIN_ALLERGY]))

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.MISSING_ALLERGY_HISTORY in outcome.review_reasons
    assert AlertType.DRUG_ALLERGY in outcome.checks_not_performed


def test_a_recorded_empty_allergy_history_is_usable(tmp_path):
    outcome = evaluate([med("Amoxicillin", "AMX-500")], [], loaded(tmp_path, [PENICILLIN_ALLERGY]))

    assert outcome.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET
    assert AlertType.DRUG_ALLERGY in outcome.checks_performed


def test_a_formulary_rule_fires(tmp_path):
    outcome = evaluate([med("Diclofenac", "DIC-50")], [], loaded(tmp_path, [FORMULARY_DICLOFENAC]))

    assert outcome.status is CheckStatus.ALERTS
    assert outcome.findings[0].type is AlertType.FORMULARY_RESTRICTION
    assert outcome.findings[0].severity is Severity.MODERATE


def test_nothing_matching_says_so_about_that_ruleset_and_nothing_more(tmp_path):
    outcome = evaluate([med("Paracetamol", "PCM-500")], [], loaded(tmp_path, [WARFARIN_IBUPROFEN]))

    assert outcome.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET
    assert outcome.ruleset is not None
    assert outcome.ruleset.ruleset_version == "test-1.0.0"


# Duplication


def test_the_same_ingredient_twice_is_referred_not_scored():
    first = med("Warfarin", "WAR-5")
    second = med("Warfarin", "WAR-5", display_name="Coumadin")
    outcome = evaluate([first, second], [], NONE)

    duplicates = [f for f in outcome.findings if f.type is AlertType.DUPLICATE_INGREDIENT]
    assert len(duplicates) == 1
    # Duplication is an identity fact. Whether it is wrong for this patient is a
    # dosing judgement, so it never gets an invented severity.
    assert duplicates[0].status is AlertStatus.NEEDS_REVIEW
    assert duplicates[0].severity is Severity.UNKNOWN
    assert (
        ReviewReason.DUPLICATION_NEEDS_CLINICAL_JUDGEMENT in duplicates[0].review_reasons
    )


def test_two_medicines_in_one_category_are_referred_as_duplicate_therapy():
    outcome = evaluate([med("Ibuprofen", "IBU-400"), med("Diclofenac", "DIC-50")], [], NONE)

    therapy = [f for f in outcome.findings if f.type is AlertType.DUPLICATE_THERAPY]
    assert len(therapy) == 1
    assert therapy[0].status is AlertStatus.NEEDS_REVIEW
    assert any("inventory records" in limit for limit in therapy[0].limitations)


def test_duplication_still_needs_review_even_with_a_ruleset_loaded(tmp_path):
    first = med("Warfarin", "WAR-5")
    second = med("Warfarin", "WAR-5", display_name="Coumadin")
    outcome = evaluate([first, second], [], loaded(tmp_path, [WARFARIN_IBUPROFEN]))

    assert outcome.status is CheckStatus.NEEDS_REVIEW


# Determinism and failure


def test_the_same_inputs_produce_the_same_finding_ids(tmp_path):
    ruleset = loaded(tmp_path, [WARFARIN_IBUPROFEN])
    medicines = [med("Warfarin", "WAR-5"), med("Ibuprofen", "IBU-400")]
    at = datetime.now(timezone.utc)

    first = evaluate(medicines, [], ruleset, evaluated_at=at)
    second = evaluate(medicines, [], ruleset, evaluated_at=at)

    assert [f.finding_id for f in first.findings] == [f.finding_id for f in second.findings]
    assert first.findings[0].model_dump() == second.findings[0].model_dump()


def test_a_rule_evaluation_that_blows_up_becomes_needs_review(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.cds.engine._rule_findings",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    outcome = evaluate([med("Warfarin", "WAR-5")], [], loaded(tmp_path, [WARFARIN_IBUPROFEN]))

    assert outcome.status is CheckStatus.NEEDS_REVIEW
    assert ReviewReason.CHECK_FAILED in outcome.review_reasons


def test_an_empty_medicine_list_with_a_ruleset_finds_nothing(tmp_path):
    outcome = evaluate([], [], loaded(tmp_path, [WARFARIN_IBUPROFEN]))
    assert outcome.status is CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET


def test_every_outcome_carries_the_identity_limitation():
    outcome = evaluate([med("Warfarin", "WAR-5")], [], NONE)
    assert any("stock list" in limit for limit in outcome.limitations)
