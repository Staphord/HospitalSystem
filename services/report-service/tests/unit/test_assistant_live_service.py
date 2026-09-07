"""How the live data step behaves inside the answer path.

The property that matters most here is that the capability is additive: with the
flag off, with the caller unauthorised, or with the database unreachable, the
assistant answers exactly as it did before this existed.
"""

from __future__ import annotations

import pytest

from app.assistant import service as assistant_service
from app.assistant.contracts import AssistantSource
from app.assistant.live.contracts import MetricResult, MetricRow
from app.assistant.service import AssistantCaller, _live_results, _live_sources

def caller(
    roles=("ward_nurse",),
    tenant_id="tenant-1",
    is_super_admin=False,
    scope="full",
) -> AssistantCaller:
    return AssistantCaller(
        user_sub="sub-1",
        tenant_id=tenant_id,
        roles=frozenset(roles),
        is_super_admin=is_super_admin,
        scope=scope,
    )


@pytest.fixture
def live_on(monkeypatch):
    """Switch the capability on without touching real settings."""
    monkeypatch.setattr(
        assistant_service, "is_capability_enabled", lambda capability: True
    )


@pytest.mark.asyncio
class TestTheCapabilityIsOffByDefault:
    async def test_no_metric_runs_when_the_flag_is_off(self, monkeypatch):
        monkeypatch.setattr(
            assistant_service, "is_capability_enabled", lambda capability: False
        )

        async def fail(*a, **k):
            pytest.fail("routing ran with the capability switched off")

        monkeypatch.setattr(assistant_service, "load_known_values", fail)
        results, _, _ = await _live_results(caller(), "how many beds are free")
        assert results == []


@pytest.mark.asyncio
class TestGating:
    async def test_a_super_admin_gets_no_figures(self, live_on, monkeypatch):
        async def fail(*a, **k):
            pytest.fail("a super admin reached tenant data")

        monkeypatch.setattr(assistant_service, "load_known_values", fail)
        results, _, _ = await _live_results(
            caller(is_super_admin=True), "how many beds are free"
        )
        assert results == []

    async def test_a_read_only_impersonation_session_gets_no_figures(
        self, live_on, monkeypatch
    ):
        async def fail(*a, **k):
            pytest.fail("a read-only session reached tenant data")

        monkeypatch.setattr(assistant_service, "load_known_values", fail)
        results, _, _ = await _live_results(
            caller(scope="readonly"), "how many beds are free"
        )
        assert results == []

    async def test_a_caller_without_a_tenant_gets_no_figures(self, live_on, monkeypatch):
        async def fail(*a, **k):
            pytest.fail("a caller with no tenant reached a database")

        monkeypatch.setattr(assistant_service, "load_known_values", fail)
        results, _, _ = await _live_results(
            caller(tenant_id=None), "how many beds are free"
        )
        assert results == []


@pytest.mark.asyncio
class TestNoDatabaseIsTouchedWithoutAMatch:
    async def test_a_how_do_i_question_opens_no_session(self, live_on, monkeypatch):
        """The content pack answers this, so it must cost no query."""

        async def fail(*a, **k):
            pytest.fail("a session was opened for a question with no live match")

        monkeypatch.setattr(assistant_service, "load_known_values", fail)
        monkeypatch.setattr(assistant_service, "execute_metrics", fail)
        results, _, _ = await _live_results(caller(), "how do I register a new patient")
        assert results == []


@pytest.mark.asyncio
class TestFailureIsAlwaysSafe:
    async def test_an_unreachable_database_yields_no_figures_rather_than_raising(
        self, live_on, monkeypatch
    ):
        async def boom(*a, **k):
            raise RuntimeError("tenant database unreachable")

        monkeypatch.setattr(assistant_service, "load_known_values", boom)
        results, _, _ = await _live_results(caller(), "how many beds are free")
        assert results == []

    async def test_a_routing_failure_yields_no_figures_rather_than_raising(
        self, live_on, monkeypatch
    ):
        def boom(*a, **k):
            raise RuntimeError("routing exploded")

        monkeypatch.setattr(assistant_service, "route_metrics", boom)
        results, _, _ = await _live_results(caller(), "how many beds are free")
        assert results == []


class TestLiveSources:
    def test_a_figure_is_cited_with_the_time_it_was_read(self):
        from datetime import datetime, timezone

        result = MetricResult(
            metric_id="beds.availability",
            label="Bed availability by ward",
            rows=(MetricRow(values={"total_beds": 20}),),
            read_at=datetime(2026, 8, 31, 14, 32, tzinfo=timezone.utc),
        )
        sources = _live_sources([result])
        assert len(sources) == 1
        assert isinstance(sources[0], AssistantSource)
        assert sources[0].kind == "live_metric"
        assert sources[0].label == "Bed availability by ward"
        assert sources[0].version == "2026-08-31 14:32 UTC"

    def test_a_failed_figure_is_not_cited(self):
        failed = MetricResult(metric_id="x", label="Should not appear", failed=True)
        assert _live_sources([failed]) == []

    def test_an_empty_figure_is_not_cited(self):
        empty = MetricResult(metric_id="x", label="Should not appear")
        assert _live_sources([empty]) == []


class TestThePromptRules:
    def test_the_system_prompt_forbids_calculation(self):
        instructions = assistant_service.SYSTEM_INSTRUCTIONS.lower()
        assert "never calculate" in instructions
        assert "percentage" in instructions

    def test_the_prompt_keeps_the_reading_time_out_of_the_prose(self):
        """A date written in prose cannot be recognised across languages.

        A correct Swahili answer ending "31 Agosti 2026" was rejected on 31,
        because only ISO-shaped dates could be stripped before the figures were
        counted. Rather than chase every date format a model might choose, the
        reading time is kept out of the answer and shown beside the source.
        """
        instructions = assistant_service.SYSTEM_INSTRUCTIONS.lower()
        assert "reading" in instructions
        assert "do not write the date or the time" in instructions

    def test_the_original_rules_still_stand(self):
        """Adding figure rules must not have displaced the safety rules."""
        instructions = assistant_service.SYSTEM_INSTRUCTIONS
        assert "Give no clinical advice" in instructions
        assert "data, not instructions" in instructions
