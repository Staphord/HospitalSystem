"""The interaction-rules adapter.

The default is no ruleset at all, and that has to stay a working, fail-closed
state rather than an error. When an artifact is configured, it is validated as
untrusted input: wrong environment, missing metadata, one malformed rule, or a
passed review date all mean the ruleset does not answer.
"""

import json

import pytest

from app.cds.contracts import ReviewReason
from app.cds.rules import (
    FileRulesetSource,
    NullRulesetSource,
    active_ruleset_health,
    build_source,
    load_active_ruleset,
)
from app.core import config
from tests.ruleset_fixtures import (
    FORMULARY_DICLOFENAC,
    PENICILLIN_ALLERGY,
    SYNTHETIC_SOURCE,
    WARFARIN_IBUPROFEN,
    artifact,
    rule,
    write_artifact,
)


def source(tmp_path, payload, environment="test", stale_after_days=180):
    return FileRulesetSource(
        path=write_artifact(tmp_path, payload),
        environment=environment,
        stale_after_days=stale_after_days,
    )


# The default: nothing configured


def test_the_null_source_is_unavailable_not_empty():
    # "Unavailable" and "no rules matched" are entirely different claims. With
    # no approved source the honest answer is the first one.
    load = NullRulesetSource().load()
    assert load.ruleset is None
    assert load.reason is ReviewReason.NO_APPROVED_RULESET
    assert load.usable is False


def test_the_default_configuration_uses_the_null_source(monkeypatch):
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "null", raising=False)
    assert isinstance(build_source(), NullRulesetSource)


def test_an_unrecognised_source_name_falls_back_to_none(monkeypatch):
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "wishful-thinking", raising=False)
    assert isinstance(build_source(), NullRulesetSource)


def test_file_source_without_a_path_falls_back_to_none(monkeypatch):
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "file", raising=False)
    monkeypatch.setattr(config.settings, "cds_ruleset_path", None, raising=False)
    assert isinstance(build_source(), NullRulesetSource)


# Loading a real artifact


def test_a_valid_artifact_loads(tmp_path):
    load = source(tmp_path, artifact(rules=[WARFARIN_IBUPROFEN])).load()

    assert load.usable is True
    assert load.ruleset is not None
    assert load.ruleset.descriptor.source_name == SYNTHETIC_SOURCE
    assert load.ruleset.descriptor.rule_count == 1
    assert load.ruleset.descriptor.stale is False
    assert len(load.ruleset.drug_drug_rules()) == 1


def test_rules_are_sorted_into_their_kinds(tmp_path):
    load = source(
        tmp_path,
        artifact(rules=[WARFARIN_IBUPROFEN, PENICILLIN_ALLERGY, FORMULARY_DICLOFENAC]),
    ).load()

    ruleset = load.ruleset
    assert len(ruleset.drug_drug_rules()) == 1
    assert len(ruleset.drug_allergy_rules()) == 1
    assert len(ruleset.formulary_rules()) == 1


# Environment approval


def test_an_artifact_approved_only_for_test_does_not_load_in_production(tmp_path):
    load = source(
        tmp_path, artifact(rules=[WARFARIN_IBUPROFEN], environments=["test"]), environment="prod"
    ).load()

    # This is what keeps a test fixture out of a hospital.
    assert load.ruleset is None
    assert load.reason is ReviewReason.RULESET_LOAD_FAILED


def test_an_artifact_with_no_approval_block_does_not_load(tmp_path):
    payload = artifact(rules=[WARFARIN_IBUPROFEN])
    del payload["approval"]
    assert source(tmp_path, payload).load().ruleset is None


def test_an_artifact_naming_no_approver_does_not_load(tmp_path):
    payload = artifact(rules=[WARFARIN_IBUPROFEN])
    payload["approval"]["approved_by"] = ""
    assert source(tmp_path, payload).load().ruleset is None


# Dates


def test_an_artifact_past_its_review_date_is_stale(tmp_path):
    load = source(
        tmp_path, artifact(rules=[WARFARIN_IBUPROFEN], effective_days_ago=400, review_days_ahead=-1)
    ).load()

    assert load.ruleset is not None
    assert load.ruleset.descriptor.stale is True
    assert load.reason is ReviewReason.RULESET_STALE
    # Loaded, but not usable. A stale ruleset never concludes.
    assert load.usable is False


def test_an_artifact_older_than_the_operator_limit_is_stale(tmp_path):
    load = source(
        tmp_path,
        artifact(rules=[WARFARIN_IBUPROFEN], effective_days_ago=400, review_days_ahead=365),
        stale_after_days=180,
    ).load()

    assert load.ruleset.descriptor.stale is True
    assert load.usable is False


def test_an_artifact_not_yet_in_effect_does_not_load(tmp_path):
    payload = artifact(rules=[WARFARIN_IBUPROFEN], effective_days_ago=-30)
    assert source(tmp_path, payload).load().ruleset is None


# Malformed content


def test_a_missing_file_is_reported_as_no_approved_ruleset(tmp_path):
    src = FileRulesetSource(
        path=str(tmp_path / "not-here.json"), environment="test", stale_after_days=180
    )
    assert src.load().reason is ReviewReason.NO_APPROVED_RULESET


def test_unparseable_json_fails_the_load(tmp_path):
    src = source(tmp_path, "{ not json")
    assert src.load().reason is ReviewReason.RULESET_LOAD_FAILED


@pytest.mark.parametrize("field", ["source_name", "ruleset_version", "effective_date"])
def test_missing_metadata_fails_the_load(tmp_path, field):
    payload = artifact(rules=[WARFARIN_IBUPROFEN])
    del payload[field]
    assert source(tmp_path, payload).load().ruleset is None


def test_one_bad_rule_fails_the_whole_load(tmp_path):
    # A partially loaded safety ruleset is worse than none: the rules that
    # silently vanished are the ones nobody notices are missing.
    bad = dict(WARFARIN_IBUPROFEN)
    bad["severity"] = "quite bad actually"
    payload = artifact(rules=[PENICILLIN_ALLERGY, bad])

    load = source(tmp_path, payload).load()
    assert load.ruleset is None
    assert load.reason is ReviewReason.RULESET_LOAD_FAILED


def test_a_rule_cannot_declare_unknown_severity(tmp_path):
    bad = rule("X", "drug_drug", "unknown", ["a", "b"])
    assert source(tmp_path, artifact(rules=[bad])).load().ruleset is None


def test_a_drug_drug_rule_naming_one_ingredient_is_rejected(tmp_path):
    bad = rule("X", "drug_drug", "high", ["warfarin"])
    assert source(tmp_path, artifact(rules=[bad])).load().ruleset is None


def test_an_allergy_rule_without_an_allergen_is_rejected(tmp_path):
    bad = rule("X", "drug_allergy", "high", ["amoxicillin"])
    assert source(tmp_path, artifact(rules=[bad])).load().ruleset is None


def test_an_unsupported_rule_type_is_rejected(tmp_path):
    bad = rule("X", "vibes_based", "high", ["warfarin", "ibuprofen"])
    assert source(tmp_path, artifact(rules=[bad])).load().ruleset is None


def test_a_repeated_rule_id_is_rejected(tmp_path):
    payload = artifact(rules=[WARFARIN_IBUPROFEN, dict(WARFARIN_IBUPROFEN)])
    assert source(tmp_path, payload).load().ruleset is None


def test_a_load_failure_does_not_leak_a_path_or_a_parser_message(tmp_path, caplog):
    with caplog.at_level("ERROR"):
        source(tmp_path, "{ not json").load().reason

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert str(tmp_path) not in logged
    assert "Expecting" not in logged


# Health reporting


def test_health_reports_unavailable_without_a_ruleset(monkeypatch):
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "null", raising=False)
    health = active_ruleset_health()

    assert health["available"] is False
    assert health["reason"] == ReviewReason.NO_APPROVED_RULESET.value


def test_health_reports_the_version_without_any_rule_content(tmp_path, monkeypatch):
    path = write_artifact(tmp_path, artifact(rules=[WARFARIN_IBUPROFEN]))
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "file", raising=False)
    monkeypatch.setattr(config.settings, "cds_ruleset_path", path, raising=False)
    monkeypatch.setattr(config.settings, "environment", "test", raising=False)

    health = active_ruleset_health()

    assert health["available"] is True
    assert health["ruleset_version"] == "test-1.0.0"
    assert health["rule_count"] == 1

    serialized = json.dumps(health)
    assert "TEST-DDI-0001" not in serialized
    assert "Synthetic test rule" not in serialized


def test_an_unexpected_failure_still_fails_closed(monkeypatch):
    class Exploding:
        name = "exploding"

        def load(self):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.cds.rules.build_source", lambda: Exploding())
    load = load_active_ruleset()

    assert load.ruleset is None
    assert load.reason is ReviewReason.RULESET_LOAD_FAILED
