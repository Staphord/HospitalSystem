"""Queue and patient-flow metrics, and the status literals they depend on.

The most dangerous defect in a metric is a wrong status literal. It does not
raise: it counts zero and returns a confident, cited, entirely false answer. A
bed metric shipped in the previous phase filtering `status = 'admitted'` when
ward-service writes 'active' would have told a nurse that nobody was admitted.

`TestStatusLiteralsAreReal` below exists to catch exactly that. The allowed
values are the Postgres enums the tenant schema declares, read from pg_enum in a
running tenant database, not copied from whatever the code happened to say.
"""

from __future__ import annotations

import re

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.catalog.flow import QUEUE_TYPE_SYNONYMS
from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import METRIC_REGISTRY
from app.assistant.live.routing import resolve_queue_type, route
from app.assistant.permissions import TENANT_STAFF_ROLES

live_registry.load_catalog()

NURSE = frozenset({"triage_nurse"})

# Allowed values are keyed by (table, column), never by column alone.
#
# Scoping by column alone would be useless here, and quietly so: 'admitted' is a
# perfectly real visit_status_enum value, so a column-only check passes the very
# bug that prompted this test - `admissions.status = 'admitted'`, where the real
# values are 'active' and 'discharged'. The table is what makes the check bite.
#
# Enum values verified against the running tenant database:
#   SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid;
# admissions.status is a plain varchar with no enum, so its values come from its
# owner: ADMISSION_ACTIVE / ADMISSION_DISCHARGED in ward-service/app/services/ward.py.
ENUM_VALUES: dict[tuple[str, str], frozenset[str]] = {
    ("queues", "status"): frozenset(
        {"waiting", "in_progress", "completed", "skipped"}
    ),
    ("queues", "queue_type"): frozenset(
        {"triage", "doctor", "lab", "radiology", "pharmacy", "billing"}
    ),
    ("queues", "priority"): frozenset(
        {"emergency", "urgent", "semi_urgent", "non_urgent"}
    ),
    ("visits", "status"): frozenset(
        {
            "registered", "triaged", "in_consultation", "in_lab", "in_pharmacy",
            "admitted", "discharged", "completed", "cancelled",
        }
    ),
    ("visits", "visit_type"): frozenset({"outpatient", "inpatient", "emergency"}),
    ("admissions", "status"): frozenset({"active", "discharged"}),
    # Billing. Plain varchars, no enum. Bills are created "open"
    # (billing-service/app/services/billing.py:74,171) and moved to "paid" or
    # "partial" in the router on payment and on adjustment
    # (app/api/v1/router.py:210,258,260) rather than in the service layer, which
    # is why grepping only app/services/ finds one of the three values.
    # It is "partial", never "partially_paid".
    ("bills", "status"): frozenset({"open", "paid", "partial"}),
    # Laboratory. Three tables, three different vocabularies, no enums. All
    # read from laboratory-service/app/services/laboratory.py.
    #
    # investigation_requests is shared with radiology: request_type decides
    # which, and laboratory-service compares it case-insensitively in every read.
    ("investigation_requests", "status"): frozenset(
        {"pending", "specimen_collected", "in_progress", "completed"}
    ),
    ("investigation_requests", "request_type"): frozenset(
        {"lab", "laboratory", "radiology"}
    ),
    # Three values, not two. 'stat' is the most urgent of them, so a metric
    # counting only 'urgent' as urgent undercounts exactly the requests that
    # cannot wait. From consultation-service/app/api/v1/schemas.py:83.
    ("investigation_requests", "urgency"): frozenset({"routine", "urgent", "stat"}),
    ("specimens", "status"): frozenset(
        {"collected", "received", "processing", "completed", "rejected"}
    ),
    # 'resulted' -> 'verified' (laboratory.py:416, 526). lab_results is never
    # 'completed'; 'completed' is what the parent *request* becomes once a
    # result is verified (laboratory.py:535). Reaching for the request's
    # vocabulary here is the mistake this entry exists to catch.
    ("lab_results", "status"): frozenset({"resulted", "verified"}),
}

# Each metric reads one table, so the FROM clause identifies which column set
# applies. JOIN is read too: a metric whose real table is reached through a join
# - or one anchored on a constant, as the billing metrics are so that an empty
# till answers zero rather than nothing - has no bare table name after FROM, and
# without this its literals would go unchecked.
_FROM = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+)", re.IGNORECASE)

# `column = 'literal'`, `column <> 'literal'` and `column IN ('a', 'b')`, the
# shapes the catalog uses. An optional table alias prefix is skipped by \b, and
# LOWER(column) is unwrapped: a case-folded comparison is still a comparison
# against a real value and must still be checked against one.
_EQUALITY = re.compile(r"\b([a-z_]+)\s*(?:=|<>|!=)\s*'([a-z_]+)'")
_IN_CLAUSE = re.compile(
    r"\b(?:LOWER\s*\(\s*)?(?:[a-z_]+\.)?([a-z_]+)\s*\)?\s+IN\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"'([a-z_]+)'")


def _table_of(metric) -> str | None:
    tables = {t.lower() for t in _FROM.findall(metric.sql)}
    return next(iter(tables)) if len(tables) == 1 else None

ALL_METRICS = sorted(METRIC_REGISTRY.values(), key=lambda m: m.metric_id)


@pytest.mark.parametrize("metric", ALL_METRICS, ids=lambda m: m.metric_id)
class TestStatusLiteralsAreReal:
    """Every status-like literal must be a value the database can actually hold."""

    def test_equality_literals_are_valid(self, metric):
        table = _table_of(metric)
        if table is None:
            return
        for column, value in _EQUALITY.findall(metric.sql):
            allowed = ENUM_VALUES.get((table, column))
            if allowed is None:
                continue
            assert value in allowed, (
                f"{metric.metric_id} compares {table}.{column} to '{value}', which "
                f"that column never holds. It would not raise; it would count zero "
                f"and read like a real answer. Allowed: {sorted(allowed)}"
            )

    def test_in_clause_literals_are_valid(self, metric):
        table = _table_of(metric)
        if table is None:
            return
        for column, body in _IN_CLAUSE.findall(metric.sql):
            allowed = ENUM_VALUES.get((table, column.lower()))
            if allowed is None:
                continue
            for value in _QUOTED.findall(body):
                assert value in allowed, (
                    f"{metric.metric_id} tests {table}.{column} IN (... '{value}' ...), "
                    f"which that column never holds. Allowed: {sorted(allowed)}"
                )


class TestQueueTypeResolution:
    @pytest.mark.parametrize(
        "question,expected",
        [
            ("how many are waiting for triage", "triage"),
            ("how many patients are waiting for the doctor", "doctor"),
            ("how long is the consultation queue", "doctor"),
            ("how many lab requests are queued", "lab"),
            ("how busy is the laboratory queue", "lab"),
            ("how many are waiting in pharmacy", "pharmacy"),
            ("how many are waiting at billing", "billing"),
            ("how many are waiting for the cashier", "billing"),
            ("how many are waiting for imaging", "radiology"),
        ],
    )
    def test_it_resolves_a_named_queue(self, question, expected):
        assert resolve_queue_type(question) == expected

    def test_it_resolves_a_swahili_queue_name(self):
        """foleni -> queue and daktari -> doctor come from the shared vocabulary."""
        assert resolve_queue_type("foleni ya daktari ina watu wangapi") == "doctor"

    def test_a_question_naming_no_queue_resolves_to_nothing(self):
        assert resolve_queue_type("how many beds are free") is None

    def test_every_synonym_maps_to_a_real_queue_type(self):
        valid = ENUM_VALUES[("queues", "queue_type")]
        for term, resolved in QUEUE_TYPE_SYNONYMS.items():
            assert resolved in valid, f"synonym {term} maps to unreal queue {resolved}"


class TestFlowRouting:
    def test_a_waiting_question_routes_to_a_queue_metric(self):
        routed = route("how many patients are waiting for triage", roles=NURSE)
        assert any(r.definition.metric_id.startswith("queue.") for r in routed)

    def test_a_named_queue_binds_the_queue_type(self):
        routed = route("how many are waiting for pharmacy", roles=NURSE)
        filtered = [r for r in routed if "queue_type" in r.definition.params]
        assert filtered, "a named queue did not reach the filtered metric"
        assert filtered[0].params.queue_type == "pharmacy"

    def test_a_queue_metric_is_skipped_when_no_queue_is_named(self):
        """An unresolved queue must never be bound; the overall metric covers it."""
        routed = route("how long are the queues today", roles=NURSE)
        assert all("queue_type" not in r.definition.params for r in routed)

    def test_a_wait_time_question_routes_to_the_wait_metric(self):
        routed = route("what is the average wait today", roles=NURSE)
        assert any(r.definition.metric_id == "queue.average_wait" for r in routed)

    def test_a_visits_question_routes_to_a_visit_metric(self):
        routed = route("how many patients registered today", roles=NURSE)
        assert any(r.definition.metric_id.startswith("visits.") for r in routed)

    def test_a_swahili_queue_question_routes(self):
        assert route("foleni ya daktari ina watu wangapi", roles=NURSE)


class TestFlowMetricShape:
    def _metric(self, metric_id):
        return METRIC_REGISTRY[metric_id]

    def test_the_single_queue_metric_always_returns_one_row(self):
        """An empty queue must answer "none waiting", not "I do not have that".

        An aggregate with no GROUP BY returns exactly one row whatever the data,
        which is what makes a useful zero possible.
        """
        sql = self._metric("queue.waiting_for_type").sql.upper()
        assert "GROUP BY" not in sql

    def test_the_overall_queue_metric_excludes_finished_entries(self):
        """A completed queue entry is not somebody waiting."""
        sql = self._metric("queue.waiting_by_type").sql
        assert "IN ('waiting', 'in_progress')" in sql

    def test_waiting_counts_are_not_date_filtered(self):
        """Waiting is a live state; a date filter would drop an overnight waiter."""
        sql = self._metric("queue.waiting_by_type").sql.lower()
        assert ":start" not in sql and ":end" not in sql

    def test_the_wait_metric_reports_minutes(self):
        metric = self._metric("queue.average_wait")
        assert "average_wait_minutes" in metric.exposed_fields
        assert "60.0" in metric.sql

    def test_flow_metrics_are_open_to_every_member_of_staff(self):
        for metric_id in (
            "queue.waiting_by_type",
            "queue.average_wait",
            "visits.in_window",
        ):
            assert self._metric(metric_id).allowed_roles == TENANT_STAFF_ROLES

    def test_no_flow_metric_is_patient_tier(self):
        for metric_id, metric in METRIC_REGISTRY.items():
            if metric_id.startswith(("queue.", "visits.")):
                assert metric.tier is MetricTier.AGGREGATE


class TestTheHttpEndpointNeverBindsAMissingParameter:
    """A dropped parameter must be an error, never a confident zero.

    The endpoint once accepted ward_name and drug_name but not queue_type, so
    asking it for the doctor queue bound NULL, matched no row, and reported an
    empty queue while twenty-seven patients were being seen. Nothing raised.
    """

    def test_every_metric_parameter_is_accepted_by_the_endpoint(self):
        import inspect

        from app.api.v1.metrics.router import read_metric

        accepted = set(inspect.signature(read_metric).parameters)
        # start and end are always resolved, and actor_sub comes from the token.
        supplied_by_the_server = {"start", "end", "actor_sub"}

        declared: set[str] = set()
        for metric in METRIC_REGISTRY.values():
            declared |= set(metric.params)

        missing = declared - accepted - supplied_by_the_server
        assert not missing, (
            "these metric parameters cannot be supplied to the HTTP endpoint, so "
            "those metrics would bind NULL and return a false zero: "
            + ", ".join(sorted(missing))
        )

    def test_valid_queue_types_match_the_enum(self):
        from app.api.v1.metrics.router import VALID_QUEUE_TYPES

        assert VALID_QUEUE_TYPES == ENUM_VALUES[("queues", "queue_type")]
