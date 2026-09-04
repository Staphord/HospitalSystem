"""Execution: read-only enforcement, bounded rows, and safe failure.

The metrics themselves are Postgres-specific (FILTER clauses, ::date casts,
EXTRACT EPOCH), so these tests drive the execution layer with fakes rather than
a database. What they check is the behaviour that must hold whatever the query
was: the transaction is pinned read-only before anything runs, rows are bounded
and projected, and no failure mode reaches the caller as an exception.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.assistant.live import execution as live_execution
from app.assistant.live.contracts import MetricParams, MetricTier
from app.assistant.live.execution import _prepare_readonly, _run_one, execute
from app.assistant.live.registry import MetricDefinition
from app.assistant.live.routing import RoutedMetric

pytestmark = pytest.mark.asyncio


METRIC = MetricDefinition(
    metric_id="test.metric",
    label="Test metric",
    tier=MetricTier.AGGREGATE,
    allowed_roles=frozenset({"ward_nurse"}),
    triggers=frozenset({"test"}),
    sql="SELECT COUNT(*) AS total_beds FROM beds",
    params=frozenset(),
    exposed_fields=frozenset({"total_beds"}),
    numeric_fields=frozenset({"total_beds"}),
    max_rows=2,
)


class FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchmany(self, size):
        return self._rows[:size]

    def fetchall(self):
        return self._rows


class FakeSession:
    """Records every statement so the read-only setup can be asserted."""

    def __init__(self, rows=None, raises=None):
        self.statements: list[str] = []
        self._rows = rows if rows is not None else []
        self._raises = raises

    async def execute(self, statement, binds=None):
        self.statements.append(str(statement))
        if self._raises is not None:
            raise self._raises
        return FakeResult(self._rows)


class TestReadOnlyEnforcement:
    async def test_the_transaction_is_pinned_read_only(self):
        """Enforcement that does not depend on a metric being written correctly."""
        session = FakeSession()
        await _prepare_readonly(session)
        assert any("SET TRANSACTION READ ONLY" in s for s in session.statements)

    async def test_a_statement_timeout_is_applied(self):
        session = FakeSession()
        await _prepare_readonly(session)
        assert any("statement_timeout" in s for s in session.statements)

    async def test_the_timeout_is_never_absurdly_small(self):
        """A misconfigured timeout must not make every query fail."""
        session = FakeSession()
        await _prepare_readonly(session)
        timeout_statement = next(s for s in session.statements if "statement_timeout" in s)
        value = int(timeout_statement.rsplit("=", 1)[1].strip())
        assert value >= 250


class TestRunningOneMetric:
    async def test_it_projects_and_records_its_figures(self):
        session = FakeSession(rows=[FakeRow({"total_beds": 12})])
        result = await _run_one(session, METRIC, MetricParams())
        assert not result.failed
        assert result.rows[0].values == {"total_beds": 12}
        assert "12" in result.figures
        assert result.read_at is not None

    async def test_it_bounds_the_rows_it_returns(self):
        rows = [FakeRow({"total_beds": n}) for n in range(10)]
        result = await _run_one(session := FakeSession(rows=rows), METRIC, MetricParams())
        assert session is not None
        assert len(result.rows) <= METRIC.max_rows

    async def test_a_database_error_becomes_a_failed_result_not_an_exception(self):
        session = FakeSession(raises=RuntimeError("connection refused"))
        result = await _run_one(session, METRIC, MetricParams())
        assert result.failed
        assert result.is_empty

    async def test_a_failed_result_carries_no_figures(self):
        session = FakeSession(raises=ValueError("boom"))
        result = await _run_one(session, METRIC, MetricParams())
        assert result.figures == frozenset()

    async def test_a_projection_escape_fails_closed_rather_than_leaking(self):
        """A query that started returning a name must yield nothing, not the name."""
        narrow = MetricDefinition(
            metric_id="test.narrow",
            label="Narrow",
            tier=MetricTier.AGGREGATE,
            allowed_roles=frozenset({"ward_nurse"}),
            triggers=frozenset({"test"}),
            sql="SELECT 1 AS a LIMIT 1",
            params=frozenset(),
            exposed_fields=frozenset({"total_beds", "available_beds"}),
        )
        session = FakeSession(rows=[FakeRow({"total_beds": 1, "full_name": "Asha"})])
        result = await _run_one(session, narrow, MetricParams())
        assert result.failed
        assert result.is_empty


class TestExecuteFailsSafely:
    async def test_an_unreachable_tenant_database_returns_failed_results(self, monkeypatch):
        """A tenant whose database is down loses its figures, not its assistant."""

        def boom(tenant_id):
            raise RuntimeError("could not resolve tenant DSN")

        monkeypatch.setattr(live_execution, "tenant_session", boom)
        routed = [RoutedMetric(definition=METRIC, params=MetricParams(), score=1)]
        results = await execute("tenant-1", routed)
        assert results and all(r.failed for r in results)

    async def test_no_tenant_means_no_query(self):
        routed = [RoutedMetric(definition=METRIC, params=MetricParams(), score=1)]
        assert await execute("", routed) == []

    async def test_nothing_routed_means_no_query(self):
        assert await execute("tenant-1", []) == []


class TestKnownValuesAreOptional:
    async def test_a_failure_to_read_known_values_is_not_an_error(self, monkeypatch):
        """Without ward names, no name-filtered metric runs. That is all."""

        def boom(tenant_id):
            raise RuntimeError("down")

        monkeypatch.setattr(live_execution, "tenant_session", boom)
        wards, drugs = await live_execution.load_known_values("tenant-1")
        assert wards == []
        assert drugs == []


class TestCaching:
    async def test_a_repeated_question_is_served_from_cache(self, monkeypatch):
        """Twenty questions a minute about beds must not be twenty scans."""
        live_execution._CACHE.clear()
        calls = {"n": 0}

        class CountingSession(FakeSession):
            async def execute(self, statement, binds=None):
                if "SELECT" in str(statement).upper() and "SET " not in str(statement).upper():
                    calls["n"] += 1
                return await super().execute(statement, binds)

        session = CountingSession(rows=[FakeRow({"total_beds": 12})])

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc):
                return False

        monkeypatch.setattr(live_execution, "tenant_session", lambda tenant_id: _Ctx())
        routed = [RoutedMetric(definition=METRIC, params=MetricParams(), score=1)]

        first = await execute("tenant-1", routed)
        before = calls["n"]
        second = await execute("tenant-1", routed)

        assert not first[0].failed
        assert calls["n"] == before, "the second identical question hit the database"
        assert second[0].read_at == first[0].read_at

    async def test_a_different_tenant_never_sees_a_cached_figure(self, monkeypatch):
        """The cache key carries the tenant, so one hospital cannot read another's."""
        live_execution._CACHE.clear()

        session_a = FakeSession(rows=[FakeRow({"total_beds": 11})])
        session_b = FakeSession(rows=[FakeRow({"total_beds": 22})])
        sessions = {"tenant-a": session_a, "tenant-b": session_b}

        def ctx(tenant_id):
            chosen = sessions[tenant_id]

            class _Ctx:
                async def __aenter__(self_inner):
                    return chosen

                async def __aexit__(self_inner, *exc):
                    return False

            return _Ctx()

        monkeypatch.setattr(live_execution, "tenant_session", ctx)
        routed = [RoutedMetric(definition=METRIC, params=MetricParams(), score=1)]

        a = await execute("tenant-a", routed)
        b = await execute("tenant-b", routed)
        assert a[0].rows[0].values["total_beds"] == 11
        assert b[0].rows[0].values["total_beds"] == 22

    async def test_different_parameters_are_cached_separately(self, monkeypatch):
        """Today's admissions must not be served for last month's question."""
        live_execution._CACHE.clear()
        windowed = MetricDefinition(
            metric_id="test.windowed",
            label="Windowed",
            tier=MetricTier.AGGREGATE,
            allowed_roles=frozenset({"ward_nurse"}),
            triggers=frozenset({"test"}),
            sql="SELECT COUNT(*) AS total_beds FROM beds "
            "WHERE d >= :start AND d <= :end",
            params=frozenset({"start", "end"}),
            exposed_fields=frozenset({"total_beds"}),
            numeric_fields=frozenset({"total_beds"}),
        )
        counter = {"n": 0}

        class CountingSession(FakeSession):
            async def execute(self, statement, binds=None):
                if "FROM beds" in str(statement):
                    counter["n"] += 1
                return FakeResult([FakeRow({"total_beds": counter["n"]})])

        session = CountingSession()

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc):
                return False

        monkeypatch.setattr(live_execution, "tenant_session", lambda t: _Ctx())

        today = date(2026, 8, 31)
        last_month = date(2026, 8, 1)
        await execute(
            "tenant-1",
            [RoutedMetric(windowed, MetricParams(start=today, end=today), 1)],
        )
        await execute(
            "tenant-1",
            [RoutedMetric(windowed, MetricParams(start=last_month, end=today), 1)],
        )
        assert counter["n"] == 2, "a different window was served from the wrong cache entry"
