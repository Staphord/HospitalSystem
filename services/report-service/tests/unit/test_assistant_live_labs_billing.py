"""Laboratory and billing metrics.

Six things here fail silently rather than loudly, so all six are pinned:

  - The outstanding balance must subtract discount_amount. billing-service
    computes total_amount - discount_amount - paid_amount (app/api/v1/router.py:208).
    The naive two-term form parses, runs, and tells a patient they owe the money
    a clinic already waived. On the data seeded during verification it read
    130,000 against a true 105,000.

  - investigation_requests is shared with radiology. request_type is 'lab' or
    'laboratory' for the laboratory and 'radiology' for imaging, so an
    unfiltered count reports every pending X-ray as a pending blood test.

  - urgency is routine / urgent / stat, and 'stat' is the most urgent of the
    three. A filter that only looks for 'urgent' undercounts exactly the
    requests that cannot wait.

  - lab_results.status is 'resulted' -> 'verified'. The parent request goes to
    'completed'. Borrowing the request's vocabulary for the result table gives a
    query that returns zero and reads like a real answer.

  - Bills carry a currency and this tenant holds more than one. Summing across
    currencies produces a number that is not money in any currency.

  - A turnaround measured over a result that precedes its own request goes
    negative. Seeding one such row made the metric answer "-2509.7 minutes",
    which is not merely wrong but impossible, stated as confidently as a real
    figure.

The status literals themselves are covered by TestStatusLiteralsAreReal in
test_assistant_live_flow.py, which now keys every one of these tables.
"""

from __future__ import annotations

import re

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import METRIC_REGISTRY
from app.assistant.live.routing import route
from app.assistant.permissions import (
    CASHIER,
    DOCTOR,
    HOSPITAL_ADMIN,
    LAB_TECHNICIAN,
    PHARMACIST,
    RADIOGRAPHER,
    RECEPTIONIST,
    TRIAGE_NURSE,
    WARD_NURSE,
)

live_registry.load_catalog()

LAB_METRICS = [m for m in METRIC_REGISTRY.values() if m.metric_id.startswith("lab.")]
BILLING_METRICS = [
    m for m in METRIC_REGISTRY.values() if m.metric_id.startswith("billing.")
]

TECH = frozenset({LAB_TECHNICIAN})
TILL = frozenset({CASHIER})
ADMIN = frozenset({HOSPITAL_ADMIN})


def _sql(metric_id: str) -> str:
    return METRIC_REGISTRY[metric_id].sql


def _squash(sql: str) -> str:
    """Collapse whitespace so an assertion is not defeated by a line break."""
    return re.sub(r"\s+", " ", sql)


def test_both_catalogs_registered():
    """A catalog that failed to import would make every test below vacuous."""
    assert len(LAB_METRICS) == 4, sorted(m.metric_id for m in LAB_METRICS)
    assert len(BILLING_METRICS) == 3, sorted(m.metric_id for m in BILLING_METRICS)


class TestTheOutstandingBalanceFormula:
    """The test that stops a patient being told they owe more than they do."""

    def test_it_subtracts_the_discount(self):
        sql = _squash(_sql("billing.unpaid"))
        assert "total_amount - b.discount_amount - b.paid_amount" in sql.replace(
            "b.total_amount", "total_amount"
        ), "the outstanding balance must use the three-term form billing-service uses"

    def test_it_is_not_the_naive_two_term_form(self):
        """total - paid ignores waivers and overstates every discounted debt."""
        sql = _squash(_sql("billing.unpaid"))
        assert "discount_amount" in sql
        assert not re.search(
            r"total_amount\s*-\s*b?\.?paid_amount", sql
        ), "the outstanding balance drops the discount term"

    def test_a_paid_bill_is_not_money_owed(self):
        assert "status <> 'paid'" in _squash(_sql("billing.unpaid"))

    def test_it_is_not_date_filtered(self):
        """A debt is a live state. A window would report the hospital owed nothing."""
        sql = _sql("billing.unpaid").lower()
        assert ":start" not in sql and ":end" not in sql


class TestMoneyIsNeverSummedAcrossCurrencies:
    def test_the_outstanding_total_is_grouped_by_currency(self):
        """This tenant holds both TZS and USD bills. Their sum is not money."""
        sql = _squash(_sql("billing.unpaid"))
        assert "GROUP BY b.currency" in sql
        assert "currency" in METRIC_REGISTRY["billing.unpaid"].exposed_fields, (
            "a total whose currency is not shown cannot be read safely"
        )

    def test_the_currency_is_not_a_figure_the_model_may_compute_with(self):
        assert "currency" not in METRIC_REGISTRY["billing.unpaid"].numeric_fields


class TestAnEmptyTillReportsZeroRatherThanNothing:
    """No rows means the figure is dropped from the prompt entirely.

    A cashier asking "how much have we taken today" at eight in the morning
    would then be told the assistant does not have that information, when the
    true and useful answer is "nothing yet". The one-row anchor on the left of
    the join guarantees at least one group whatever the data holds.
    """

    @pytest.mark.parametrize(
        "metric_id", ["billing.collected", "billing.unpaid"], ids=lambda m: m
    )
    def test_the_grouped_money_metrics_are_anchored(self, metric_id):
        sql = _squash(_sql(metric_id))
        assert "FROM (SELECT 1)" in sql
        assert "LEFT JOIN" in sql

    @pytest.mark.parametrize(
        "metric_id",
        ["billing.bill_status_summary", "lab.pending_count",
         "lab.requests_in_window", "lab.critical_unverified", "lab.turnaround"],
        ids=lambda m: m,
    )
    def test_the_single_row_metrics_never_group(self, metric_id):
        """An aggregate with no GROUP BY returns exactly one row whatever the data."""
        assert "GROUP BY" not in _sql(metric_id).upper()
        assert METRIC_REGISTRY[metric_id].max_rows == 1


class TestTheModelIsNeverLeftToDoTheArithmetic:
    """A figure the model worked out itself is refused by validate_figures.

    Refusing it replaces a correct answer with the plain fallback listing, so
    every part a model would otherwise compute is supplied instead: the total
    alongside the breakdown, and every status count alongside the overall count.
    """

    def test_the_collection_total_is_supplied_not_left_to_be_added_up(self):
        metric = METRIC_REGISTRY["billing.collected"]
        assert "total_collected" in metric.exposed_fields
        assert "total_collected" in metric.numeric_fields
        assert "OVER ()" in _squash(metric.sql)

    def test_turnaround_supplies_both_units_so_neither_is_converted(self):
        metric = METRIC_REGISTRY["lab.turnaround"]
        assert {"average_turnaround_minutes", "average_turnaround_hours"} <= (
            metric.exposed_fields
        )


class TestTurnaroundIsUsable:
    def test_it_is_reported_in_minutes_or_hours_never_seconds(self):
        metric = METRIC_REGISTRY["lab.turnaround"]
        for field in metric.exposed_fields:
            assert not field.endswith("_seconds"), (
                f"{field} would report a turnaround as a number of seconds, "
                f"which nobody can act on"
            )
        assert any("minutes" in f or "hours" in f for f in metric.exposed_fields)

    def test_it_divides_the_epoch_rather_than_reporting_it_raw(self):
        sql = _squash(_sql("lab.turnaround"))
        assert "/ 60.0" in sql and "/ 3600.0" in sql

    def test_it_rounds_in_the_database(self):
        """The figure shown must be the exact figure recorded as supplied."""
        assert "ROUND(" in _squash(_sql("lab.turnaround"))

    def test_a_result_that_precedes_its_own_request_is_excluded(self):
        """Nothing in the schema enforces the order, and one such row goes negative.

        Seeding a result timestamped before its request turned the answer into
        "average turnaround: -2509.7 minutes" - impossible, and stated with the
        same confidence as a real figure.
        """
        assert "r.resulted_at >= q.requested_at" in _squash(_sql("lab.turnaround"))


class TestTheLabMetricsReadTheLaboratoryAndNotRadiology:
    """investigation_requests is shared. request_type is what separates them."""

    @pytest.mark.parametrize(
        "metric_id",
        ["lab.pending_count", "lab.requests_in_window", "lab.turnaround"],
        ids=lambda m: m,
    )
    def test_every_request_metric_filters_to_lab_request_types(self, metric_id):
        sql = _squash(_sql(metric_id))
        assert re.search(
            r"LOWER\((?:[a-z]+\.)?request_type\) IN \('lab', 'laboratory'\)", sql
        ), (
            f"{metric_id} counts the whole investigation_requests table, so every "
            f"pending X-ray would be reported as a pending lab test"
        )

    @pytest.mark.parametrize(
        "metric_id",
        ["lab.pending_count", "lab.requests_in_window", "lab.turnaround"],
        ids=lambda m: m,
    )
    def test_the_filter_is_case_folded_as_laboratory_service_folds_it(self, metric_id):
        """laboratory-service compares func.lower(request_type) in every read."""
        assert "LOWER(" in _squash(_sql(metric_id))

    def test_no_lab_metric_names_radiology(self):
        for metric in LAB_METRICS:
            assert "radiology" not in metric.sql.lower()


class TestTheLabStatusVocabulariesAreNotMixed:
    def test_the_outstanding_backlog_uses_request_statuses(self):
        sql = _squash(_sql("lab.pending_count"))
        assert "status IN ('pending', 'specimen_collected', 'in_progress')" in sql

    def test_a_finished_request_is_not_outstanding(self):
        assert "'completed'" not in _squash(_sql("lab.pending_count"))

    def test_the_critical_figure_does_not_borrow_a_status_literal_at_all(self):
        """verified_at and status='verified' are written together, so the
        timestamp is the same test and cannot be spelt wrongly."""
        sql = _squash(_sql("lab.critical_unverified"))
        assert "verified_at IS NULL" in sql
        assert "'verified'" not in sql
        assert "'completed'" not in sql, (
            "lab_results is never 'completed'; that is the parent request's status"
        )

    def test_urgency_counts_stat_as_urgent(self):
        """'stat' is the most urgent of routine/urgent/stat, not a fourth idea."""
        sql = _squash(_sql("lab.pending_count"))
        assert "urgency IN ('urgent', 'stat')" in sql
        assert "urgency = 'urgent'" not in sql


class TestTheLabMetricsCarryNoClinicalContent:
    """The registry test covers this over the whole catalog. It is restated for
    the lab metrics because this is the catalog where the tempting column is one
    join away: a result value, a reference range, a clinical history."""

    _NEVER = (
        "result_value",
        "result_notes",
        "reference_range",
        "clinical_history",
        "rejection_reason",
        "full_name",
        "patient_number",
        "test_name",
    )

    @pytest.mark.parametrize("metric", LAB_METRICS, ids=lambda m: m.metric_id)
    def test_it_selects_no_result_value_or_clinical_narrative(self, metric):
        sql = metric.sql.lower()
        for column in self._NEVER:
            assert re.search(r"\b" + column + r"\b", sql) is None, (
                f"{metric.metric_id} reads {column}, which is a clinical value "
                f"or an identifier rather than a count"
            )

    @pytest.mark.parametrize("metric", LAB_METRICS, ids=lambda m: m.metric_id)
    def test_it_never_joins_to_a_patient(self, metric):
        assert re.search(r"\bpatients\b", metric.sql.lower()) is None

    def test_no_lab_metric_is_patient_tier(self):
        for metric in LAB_METRICS:
            assert metric.tier is MetricTier.AGGREGATE


class TestTheBillingMetricsCarryNoClinicalContent:
    @pytest.mark.parametrize("metric", BILLING_METRICS, ids=lambda m: m.metric_id)
    def test_it_never_reads_a_bill_line(self, metric):
        """bill_items.description names the procedure a charge was raised for."""
        assert "bill_items" not in metric.sql.lower()
        assert "description" not in metric.sql.lower()

    @pytest.mark.parametrize("metric", BILLING_METRICS, ids=lambda m: m.metric_id)
    def test_it_never_names_a_patient_or_a_cashier(self, metric):
        sql = metric.sql.lower()
        for column in ("patient_id", "full_name", "patient_number", "cashier_id"):
            assert re.search(r"\b" + column + r"\b", sql) is None

    def test_no_billing_metric_is_patient_tier(self):
        for metric in BILLING_METRICS:
            assert metric.tier is MetricTier.AGGREGATE


class TestBillingPermissions:
    """Money is cashier and administrator work. What a patient owes is not a
    clinical fact and does not travel with clinical seniority."""

    def test_a_cashier_reaches_every_billing_metric(self):
        for metric in BILLING_METRICS:
            assert metric.is_permitted(TILL), f"{metric.metric_id} refused a cashier"

    def test_a_hospital_admin_reaches_every_billing_metric(self):
        for metric in BILLING_METRICS:
            assert metric.is_permitted(ADMIN)

    @pytest.mark.parametrize(
        "role",
        [PHARMACIST, DOCTOR, WARD_NURSE, TRIAGE_NURSE, RECEPTIONIST,
         LAB_TECHNICIAN, RADIOGRAPHER],
    )
    def test_no_one_else_reaches_a_billing_metric(self, role):
        for metric in BILLING_METRICS:
            assert not metric.is_permitted(frozenset({role})), (
                f"{metric.metric_id} was reachable by {role}"
            )

    def test_a_super_admin_reaches_no_billing_metric(self):
        for metric in BILLING_METRICS:
            assert not metric.is_permitted(TILL, is_super_admin=True)

    @pytest.mark.parametrize("role", [PHARMACIST, DOCTOR, WARD_NURSE, TRIAGE_NURSE])
    def test_a_money_question_from_anyone_else_reaches_no_money(self, role):
        """Not necessarily nothing: "how much have we collected today" shares
        "today" and "many" with visits.in_window, which every member of staff may
        read. What must hold is that no *figure about money* comes back."""
        for question in (
            "how much have we collected today",
            "how much is outstanding on unpaid bills",
        ):
            routed = route(question, roles=frozenset({role}))
            assert all(
                not r.definition.metric_id.startswith("billing.") for r in routed
            ), f"{role} reached a billing figure by asking: {question}"


class TestLabPermissions:
    def test_a_lab_technician_reaches_every_lab_metric(self):
        for metric in LAB_METRICS:
            assert metric.is_permitted(TECH), f"{metric.metric_id} refused a technician"

    def test_a_hospital_admin_reaches_every_lab_metric(self):
        for metric in LAB_METRICS:
            assert metric.is_permitted(ADMIN)

    def test_a_doctor_may_see_the_backlog_and_the_critical_alert(self):
        """A doctor waiting on a result decides whether to keep a patient waiting."""
        for metric_id in ("lab.pending_count", "lab.critical_unverified"):
            assert METRIC_REGISTRY[metric_id].is_permitted(frozenset({DOCTOR}))

    def test_a_doctor_does_not_get_laboratory_performance(self):
        """Turnaround and daily volumes are the laboratory's own management figures."""
        for metric_id in ("lab.turnaround", "lab.requests_in_window"):
            assert not METRIC_REGISTRY[metric_id].is_permitted(frozenset({DOCTOR}))

    @pytest.mark.parametrize("role", [CASHIER, PHARMACIST, RECEPTIONIST])
    def test_unrelated_roles_reach_no_lab_metric(self, role):
        for metric in LAB_METRICS:
            assert not metric.is_permitted(frozenset({role})), (
                f"{metric.metric_id} was reachable by {role}"
            )

    def test_a_super_admin_reaches_no_lab_metric(self):
        for metric in LAB_METRICS:
            assert not metric.is_permitted(TECH, is_super_admin=True)


class TestRouting:
    def test_a_backlog_question_routes_to_the_lab_metrics(self):
        routed = route("how many lab tests are still pending", roles=TECH)
        assert any(r.definition.metric_id.startswith("lab.") for r in routed)

    def test_a_critical_result_question_routes(self):
        routed = route("are there any critical results not yet verified", roles=TECH)
        assert any(
            r.definition.metric_id == "lab.critical_unverified" for r in routed
        )

    def test_a_turnaround_question_routes(self):
        routed = route("what is the average lab turnaround time today", roles=TECH)
        assert any(r.definition.metric_id == "lab.turnaround" for r in routed)

    def test_a_collection_question_routes_to_billing(self):
        routed = route("how much have we collected today", roles=TILL)
        assert any(r.definition.metric_id.startswith("billing.") for r in routed)

    def test_an_outstanding_question_routes_to_the_unpaid_metric(self):
        routed = route("how much is still outstanding on unpaid bills", roles=TILL)
        assert any(r.definition.metric_id == "billing.unpaid" for r in routed)

    def test_a_swahili_billing_question_routes(self):
        """malipo -> payment/billing comes from the shared vocabulary map."""
        assert route("malipo ya leo ni kiasi gani", roles=TILL)

    def test_a_swahili_unpaid_question_routes(self):
        """The verb is conjugated past anything the shared map recognises.

        "ni kiasi gani bado hakijalipwa" carries no term language.py translates,
        so it matched nothing at all until "kiasi" became a trigger in its own
        right. It answered "I do not have information about that" while 95,000
        was outstanding.
        """
        routed = route("ni kiasi gani bado hakijalipwa", roles=TILL)
        assert any(r.definition.metric_id.startswith("billing.") for r in routed)

    def test_a_swahili_lab_question_routes(self):
        """vipimo -> laboratory/results comes from the shared vocabulary map."""
        assert route("vipimo vingapi bado maabara", roles=TECH)

    def test_a_lab_question_never_reaches_a_billing_metric_for_a_technician(self):
        routed = route("how many lab tests are pending", roles=TECH)
        assert all(
            not r.definition.metric_id.startswith("billing.") for r in routed
        )

    @pytest.mark.parametrize(
        "word", ["today", "yesterday", "week", "month", "leo", "jana", "wiki", "mwezi"]
    )
    def test_no_lab_or_billing_metric_triggers_on_a_bare_date_word(self, word):
        """A date word carries no topic, and resolve_window already reads it.

        Triggering on one lets any question mentioning "today" drag an unrelated
        figure into the prompt, where the model will find a use for it. A lab
        technician asking "how much have we collected today" was answered
        "Specimen collected: 1" - a real number from a real metric, repurposed
        for a question about money because "today" was the only word that matched.
        """
        for metric in LAB_METRICS + BILLING_METRICS:
            assert word not in metric.triggers, (
                f"{metric.metric_id} triggers on the bare date word '{word}'"
            )

    def test_a_money_question_reaches_no_lab_figure(self):
        routed = route("how much have we collected today", roles=TECH)
        assert all(
            not r.definition.metric_id.startswith("lab.") for r in routed
        ), "a question about money pulled in a laboratory figure"

    def test_a_lab_question_still_routes_without_its_date_words(self):
        """Removing the date words must not cost the metric its real question."""
        routed = route("how many lab requests were raised today", roles=TECH)
        assert any(
            r.definition.metric_id == "lab.requests_in_window" for r in routed
        )

    def test_a_collection_question_still_routes_without_its_date_words(self):
        routed = route("how much did we collect today", roles=TILL)
        assert any(r.definition.metric_id == "billing.collected" for r in routed)

    def test_the_new_metrics_declare_no_parameter_the_endpoint_cannot_supply(self):
        """A declared-but-unsupplied parameter binds NULL and returns a false zero.

        test_assistant_live_flow.py asserts this over the whole registry; it is
        restated here so a lab or billing parameter added later fails in the file
        that added it.
        """
        import inspect

        from app.api.v1.metrics.router import read_metric

        accepted = set(inspect.signature(read_metric).parameters)
        supplied_by_the_server = {"start", "end", "actor_sub"}
        for metric in LAB_METRICS + BILLING_METRICS:
            missing = set(metric.params) - accepted - supplied_by_the_server
            assert not missing, (
                f"{metric.metric_id} needs {sorted(missing)}, which the endpoint "
                f"cannot supply, so it would bind NULL and answer zero"
            )
