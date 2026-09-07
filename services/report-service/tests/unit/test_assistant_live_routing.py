"""Routing decides which live metrics run. The model is never consulted.

The properties that matter here are that routing is deterministic, that it is
fail-closed, and that a patient-tier metric is unreachable without an explicit
identifier in the question.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.routing import (
    MAX_WINDOW_DAYS,
    extract_identifiers,
    match_known_value,
    resolve_window,
    route,
)

live_registry.load_catalog()

WARD_NURSE = frozenset({"ward_nurse"})
CASHIER = frozenset({"cashier"})
ANCHOR = date(2026, 8, 31)


class TestMetricSelection:
    def test_a_bed_question_routes_to_bed_metrics(self):
        routed = route("how many beds are free", roles=WARD_NURSE)
        assert routed
        assert any(r.definition.metric_id.startswith("beds.") for r in routed)

    def test_a_swahili_bed_question_routes_the_same_way(self):
        """Swahili reaches the same metric through the shared vocabulary map."""
        english = route("how many beds are free", roles=WARD_NURSE)
        swahili = route("kuna vitanda vingapi wazi", roles=WARD_NURSE)
        assert swahili, "a Swahili bed question routed to nothing"
        assert {r.definition.metric_id for r in swahili} & {
            r.definition.metric_id for r in english
        }

    def test_an_unrelated_question_routes_to_nothing(self):
        """No match means the answer falls back to the content pack, as before."""
        assert route("how do I register a new patient", roles=WARD_NURSE) == []

    def test_an_empty_question_routes_to_nothing(self):
        assert route("", roles=WARD_NURSE) == []

    def test_a_caller_with_no_roles_routes_to_nothing(self):
        assert route("how many beds are free", roles=frozenset()) == []

    def test_a_super_admin_routes_to_nothing(self):
        assert route("how many beds are free", roles=WARD_NURSE, is_super_admin=True) == []

    def test_a_role_without_the_metric_routes_to_nothing(self):
        """A cashier has no business reading ward bed states."""
        routed = route("how many beds are free", roles=CASHIER)
        assert all(not r.definition.metric_id.startswith("beds.") for r in routed)

    def test_selection_is_bounded(self):
        routed = route(
            "beds free empty available occupied ward admitted discharged stay average",
            roles=WARD_NURSE,
            limit=3,
        )
        assert len(routed) <= 3

    def test_selection_is_deterministic(self):
        question = "how many beds are free today"
        first = [r.definition.metric_id for r in route(question, roles=WARD_NURSE)]
        second = [r.definition.metric_id for r in route(question, roles=WARD_NURSE)]
        assert first == second


class TestWardFilteredMetricsNeedAKnownWard:
    def test_a_ward_metric_is_skipped_when_no_ward_is_known(self):
        """An invented ward name must never reach a query."""
        routed = route("how many beds are free in Narnia", roles=WARD_NURSE)
        assert all(
            "ward_name" not in r.definition.params for r in routed
        ), "a ward-filtered metric ran without a resolved ward"

    def test_a_ward_metric_runs_once_the_ward_exists(self):
        routed = route(
            "how many beds are free in Maternity",
            roles=WARD_NURSE,
            known_wards=["Maternity", "Surgical"],
        )
        filtered = [r for r in routed if "ward_name" in r.definition.params]
        assert filtered, "a real ward name did not reach the filtered metric"
        assert filtered[0].params.ward_name == "Maternity"


class TestKnownValueMatching:
    def test_it_matches_a_value_that_exists(self):
        assert match_known_value("beds free in Maternity", ["Maternity"]) == "Maternity"

    def test_it_is_case_insensitive(self):
        assert match_known_value("beds in maternity ward", ["Maternity"]) == "Maternity"

    def test_it_returns_nothing_for_a_value_that_does_not_exist(self):
        assert match_known_value("beds free in Narnia", ["Maternity"]) is None

    def test_the_longest_match_wins(self):
        """"Maternity Annex" must not be answered with "Maternity"."""
        matched = match_known_value(
            "beds in Maternity Annex", ["Maternity", "Maternity Annex"]
        )
        assert matched == "Maternity Annex"

    def test_an_empty_candidate_list_matches_nothing(self):
        assert match_known_value("beds in Maternity", []) is None


class TestIdentifierExtraction:
    def test_a_patient_number_is_found(self):
        # PT-YYYYMMDD-NNNN, from patient-service/app/services/patient_number.py:49.
        # This assertion used to name "OPD-2026-0142", a format no service in the
        # system generates, and the pattern it was written against could not
        # match a real one - so the patient tier was unreachable while this
        # passed. test_assistant_live_aliases.py covers the formats in full.
        patient, _ = extract_identifiers("where is patient PT-20260829-0001 now")
        assert patient == "PT-20260829-0001"

    def test_a_question_with_no_identifier_yields_none(self):
        assert extract_identifiers("how many patients are waiting") == (None, None)

    def test_a_patient_tier_metric_never_runs_without_an_identifier(self):
        """The general enquiry route must not reach one patient's record."""
        from app.assistant.live.contracts import MetricTier

        for question in (
            "how many patients are waiting",
            "where are my patients",
            "show me patient details",
        ):
            routed = route(question, roles=WARD_NURSE, actor_sub="sub-1")
            assert all(
                r.definition.tier is not MetricTier.PATIENT for r in routed
            ), f"a patient-tier metric was reachable from: {question}"


class TestDateWindows:
    def test_it_defaults_to_today(self):
        assert resolve_window("how many beds are free", today=ANCHOR) == (ANCHOR, ANCHOR)

    def test_it_understands_this_week(self):
        start, end = resolve_window("admissions this week", today=ANCHOR)
        assert (end - start).days == 7

    def test_it_understands_this_month(self):
        start, end = resolve_window("admissions this month", today=ANCHOR)
        assert (end - start).days == 30

    def test_it_understands_swahili_today(self):
        assert resolve_window("wagonjwa leo", today=ANCHOR) == (ANCHOR, ANCHOR)

    @pytest.mark.parametrize(
        "question", ["admissions", "admissions this week", "admissions this month"]
    )
    def test_every_window_is_bounded(self, question):
        start, end = resolve_window(question, today=ANCHOR)
        assert start <= end
        assert (end - start).days <= MAX_WINDOW_DAYS
