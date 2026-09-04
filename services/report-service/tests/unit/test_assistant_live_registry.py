"""Structural guarantees over every registered live metric.

These are the tests that make "the assistant reads allowed columns only" a
property of the build rather than a claim in a document. They run over the whole
registry, so a metric added later is covered without anyone remembering to add
a test for it: a new metric that reaches for a name, a phone number, or clinical
narrative fails here instead of shipping.
"""

from __future__ import annotations

import re

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.contracts import MetricParams, MetricTier
from app.assistant.live.registry import (
    DERIVED_ONLY_COLUMNS,
    FORBIDDEN_SQL_COLUMNS,
    METRIC_REGISTRY,
    PSEUDONYMISED_COLUMNS,
    MetricDefinition,
    permitted_metrics,
)
from app.assistant.permissions import SUPER_ADMIN, TENANT_STAFF_ROLES

live_registry.load_catalog()

ALL_METRICS = sorted(METRIC_REGISTRY.values(), key=lambda m: m.metric_id)

# Statements a read-only metric may never contain, whatever else is true of it.
_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|MERGE)\b",
    re.IGNORECASE,
)

# An aggregate metric must actually aggregate, or bound its rows, or it is a
# table scan wearing a metric's clothes.
_AGGREGATE = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX|ROUND)\s*\(", re.IGNORECASE)


def test_the_registry_is_not_empty():
    """A catalog that silently failed to import would make every other test vacuous."""
    assert ALL_METRICS, "no metrics registered; did load_catalog import the catalog?"


@pytest.mark.parametrize("metric", ALL_METRICS, ids=lambda m: m.metric_id)
class TestEveryMetricIsStructurallySafe:
    def test_it_names_no_forbidden_column(self, metric: MetricDefinition):
        """No metric may select a name, a contact detail, or clinical narrative.

        Patient-tier metrics are allowed the pseudonymised columns, because
        execution replaces those values with an opaque label before any prompt
        is assembled. Every other tier is allowed neither.
        """
        sql = metric.sql.lower()
        # A patient-tier metric may name a derived-only column inside the
        # expression that buckets it - the age band cannot be computed without
        # date_of_birth - but never expose the value. The second half of that
        # guarantee is asserted by test_it_exposes_no_derived_only_column below.
        forbidden = FORBIDDEN_SQL_COLUMNS
        if metric.tier is MetricTier.PATIENT:
            forbidden = forbidden - DERIVED_ONLY_COLUMNS
        for column in forbidden:
            assert (
                re.search(r"\b" + re.escape(column) + r"\b", sql) is None
            ), f"{metric.metric_id} selects forbidden column {column}"

        if metric.tier is not MetricTier.PATIENT:
            for column in PSEUDONYMISED_COLUMNS:
                assert (
                    re.search(r"\b" + re.escape(column) + r"\b", sql) is None
                ), (
                    f"{metric.metric_id} is tier {metric.tier.value} and may not "
                    f"name {column}; only a patient-tier metric may, and only "
                    f"through the alias path"
                )

    def test_it_exposes_no_derived_only_column(self, metric: MetricDefinition):
        """A column read to derive a coarse value must never be a value itself.

        date_of_birth may be bucketed into an age band; the band may leave, the
        date may not. exposed_fields is deny-by-default, so this is what keeps
        the SQL exemption above from widening into an exposure.
        """
        leaked = metric.exposed_fields & DERIVED_ONLY_COLUMNS
        assert not leaked, (
            f"{metric.metric_id} exposes {sorted(leaked)}, which may only be "
            f"read to derive a coarser value"
        )

    def test_it_only_reads(self, metric: MetricDefinition):
        assert _WRITE_KEYWORDS.search(metric.sql) is None, (
            f"{metric.metric_id} contains a write statement"
        )

    def test_it_aggregates_or_bounds_its_rows(self, metric: MetricDefinition):
        sql = metric.sql.upper()
        assert _AGGREGATE.search(metric.sql) or "LIMIT" in sql, (
            f"{metric.metric_id} neither aggregates nor limits, so it could "
            f"return an unbounded number of rows"
        )

    def test_its_declared_params_match_the_binds_it_uses(self, metric: MetricDefinition):
        """A metric may neither ignore a bind it declared nor use one it did not.

        This is what stops a parameter being silently dropped: a metric that
        declares `ward_name` but forgot to reference it would otherwise return
        every ward while appearing to filter.
        """
        assert metric.declared_binds() == metric.params, (
            f"{metric.metric_id} declares params {sorted(metric.params)} but its "
            f"SQL binds {sorted(metric.declared_binds())}"
        )

    def test_its_numeric_fields_are_exposed_fields(self, metric: MetricDefinition):
        """A figure the validator trusts must be one the model was actually shown."""
        assert metric.numeric_fields <= metric.exposed_fields, (
            f"{metric.metric_id} records numeric fields it does not expose"
        )

    def test_it_exposes_at_least_one_field(self, metric: MetricDefinition):
        assert metric.exposed_fields, f"{metric.metric_id} exposes nothing"

    def test_it_is_gated_to_real_roles(self, metric: MetricDefinition):
        assert metric.allowed_roles, f"{metric.metric_id} is not role-gated at all"
        unknown = metric.allowed_roles - TENANT_STAFF_ROLES
        assert not unknown, f"{metric.metric_id} names unknown roles {sorted(unknown)}"
        assert SUPER_ADMIN not in metric.allowed_roles

    def test_it_has_routing_triggers(self, metric: MetricDefinition):
        assert metric.triggers, f"{metric.metric_id} can never be routed to"

    def test_its_row_ceiling_is_bounded(self, metric: MetricDefinition):
        assert 0 < metric.max_rows <= 100


class TestProjectionCannotWiden:
    """The deny-by-default projection is the last line before the prompt."""

    def _metric(self, exposed: set[str]) -> MetricDefinition:
        return MetricDefinition(
            metric_id="test.projection",
            label="Test",
            tier=MetricTier.AGGREGATE,
            allowed_roles=frozenset({"doctor"}),
            triggers=frozenset({"test"}),
            sql="SELECT 1 AS a LIMIT 1",
            params=frozenset(),
            exposed_fields=frozenset(exposed),
        )

    def test_an_extra_column_is_dropped_not_leaked(self):
        """A query returning more than the allowlist must not widen the answer."""
        metric = self._metric({"total_beds"})
        projected = metric.project({"total_beds": 4, "full_name": "Asha Mwinyi"})
        assert projected == {"total_beds": 4}
        assert "full_name" not in projected

    def test_a_missing_column_raises_rather_than_silently_shrinking(self):
        """A metric whose query stopped returning a column is a defect, not a smaller answer."""
        metric = self._metric({"total_beds", "available_beds"})
        with pytest.raises(RuntimeError, match="escaped its field allowlist"):
            metric.project({"total_beds": 4})


class TestRoleGating:
    def test_a_super_admin_reaches_no_metric(self):
        """Super admins administer tenants and never read tenant data."""
        assert permitted_metrics(frozenset({"hospital_admin"}), is_super_admin=True) == []
        assert permitted_metrics(frozenset({SUPER_ADMIN})) == []

    def test_a_caller_with_no_roles_reaches_no_metric(self):
        assert permitted_metrics(frozenset()) == []

    def test_permitted_metrics_is_stably_ordered(self):
        roles = frozenset({"hospital_admin"})
        assert [m.metric_id for m in permitted_metrics(roles)] == sorted(
            m.metric_id for m in permitted_metrics(roles)
        )


class TestParamBinding:
    def test_only_declared_binds_are_sent(self):
        """A metric receives the parameters it declared and nothing else."""
        params = MetricParams(ward_name="Maternity", drug_name="Amoxicillin")
        assert params.as_binds(frozenset({"ward_name"})) == {"ward_name": "Maternity"}

    def test_an_undeclared_bind_is_absent_not_none(self):
        params = MetricParams()
        assert params.as_binds(frozenset()) == {}
