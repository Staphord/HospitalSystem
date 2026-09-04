from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from cachetools import TTLCache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.live.aliases import ALIAS_FIELD, AliasTable
from app.assistant.live.contracts import MetricParams, MetricResult, MetricRow, MetricTier
from app.assistant.live.registry import MetricDefinition
from app.assistant.live.routing import RoutedMetric
from app.core.config import settings
from app.db.tenant import tenant_session

logger = logging.getLogger("assistant.live")

# Results are cached briefly per tenant, metric and parameter set. The chat rate
# limit allows twenty questions a minute; without this, twenty questions about
# bed availability would be twenty scans. Every figure carries the time it was
# read, so a cached figure is visibly a cached figure rather than a silent one.
_CACHE: TTLCache[tuple, MetricResult] = TTLCache(
    maxsize=512,
    ttl=max(1, int(getattr(settings, "assistant_live_data_cache_seconds", 30))),
)


def _cache_key(
    tenant_id: str, definition: MetricDefinition, binds: dict[str, Any]
) -> tuple:
    return (
        tenant_id,
        definition.metric_id,
        tuple(sorted((k, str(v)) for k, v in binds.items())),
    )


def format_value(value: Any) -> str:
    """Render one cell as the exact string the model will be shown.

    The same function produces the prompt text and the set of figures the answer
    validator accepts, so the two can never drift apart. A figure the model was
    shown as "12.0" must not then be validated against "12".
    """
    if value is None:
        return "not recorded"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        # Decimal.normalize renders small integers in exponent form ("1E+1"),
        # so integral values are formatted explicitly instead.
        normalized = value.normalize()
        if normalized == normalized.to_integral_value():
            return format(normalized, "f")
        return str(normalized)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(round(value, 2))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


async def _prepare_readonly(session: AsyncSession) -> None:
    """Pin the transaction read-only and bound how long a query may run.

    This is the enforcement that does not depend on a metric being written
    correctly: a definition that somehow carried a write cannot commit one, and
    a pathological query releases its connection instead of holding it.
    """
    timeout_ms = int(
        float(getattr(settings, "assistant_live_data_timeout_seconds", 3.0)) * 1000
    )
    await session.execute(text("SET TRANSACTION READ ONLY"))
    await session.execute(
        text("SET LOCAL statement_timeout = " + str(max(250, timeout_ms)))
    )


def _pseudonymise(projected: dict[str, Any], aliases: AliasTable | None) -> dict[str, Any]:
    """Replace a patient's identity with a per-request label.

    This is the single point where a patient-tier row stops being about a named
    person. It runs between the projection and the MetricRow, so nothing
    downstream - the prompt block, the figure set, the sources, the fallback
    listing - ever holds a name to leak.

    A missing alias table is a defect, not a runtime condition, and it raises:
    the caller turns that into a failed, empty result, which is the only safe
    way to fail here. Answering without the table would mean sending the name.
    """
    if aliases is None:
        raise RuntimeError("a patient-tier metric ran with no alias table")

    real_id = projected.pop("patient_number", None)
    display_name = projected.pop("full_name", None)
    if real_id is None or display_name is None:
        raise RuntimeError(
            "a patient-tier metric must expose full_name and patient_number so "
            "an alias can be issued for them"
        )
    projected[ALIAS_FIELD] = aliases.issue(str(real_id), str(display_name))
    return projected


async def _run_one(
    session: AsyncSession,
    definition: MetricDefinition,
    params: MetricParams,
    aliases: AliasTable | None = None,
) -> MetricResult:
    """Run one metric. Never raises; a failure is an empty, safe result."""
    read_at = datetime.now(timezone.utc)
    try:
        binds = params.as_binds(definition.params)
        result = await session.execute(text(definition.sql), binds)
        raw_rows = result.fetchmany(definition.max_rows)

        rows: list[MetricRow] = []
        figures: set[str] = set()
        for raw in raw_rows:
            projected = definition.project(raw)
            if definition.tier is MetricTier.PATIENT:
                projected = _pseudonymise(projected, aliases)
            rows.append(MetricRow(values=projected))
            for name in definition.numeric_fields:
                if name in projected:
                    figures.add(format_value(projected[name]))

        subject = None
        if definition.tier is MetricTier.PATIENT:
            # Whichever identifier this metric was keyed by. Both come from the
            # staff member's own question; see MetricResult.subject.
            subject = params.patient_number or params.visit_number

        return MetricResult(
            metric_id=definition.metric_id,
            label=definition.label,
            rows=tuple(rows),
            read_at=read_at,
            figures=frozenset(figures),
            subject=subject,
        )
    except RuntimeError:
        # A projection escaped its allowlist. That is a defect in a metric
        # definition rather than a runtime condition, so it is logged loudly.
        # It still must not leak a row, so the result is empty like any failure.
        logger.exception(
            "metric projection escaped its allowlist metric_id=%s", definition.metric_id
        )
        return MetricResult(
            metric_id=definition.metric_id, label=definition.label, failed=True
        )
    except Exception:
        # No database error, DSN, or stack trace may reach a reader. The answer
        # degrades to whatever the content pack supports, as if the question had
        # matched no figure at all.
        logger.warning("metric failed metric_id=%s", definition.metric_id)
        return MetricResult(
            metric_id=definition.metric_id, label=definition.label, failed=True
        )


async def load_known_values(tenant_id: str) -> tuple[list[str], list[str]]:
    """Read the ward and drug names that exist in this tenant.

    Routing matches a question against these before binding anything, so a value
    invented in a question can never reach a query. Failure is not an error: an
    empty list simply means no name-filtered metric will run.
    """
    wards: list[str] = []
    drugs: list[str] = []
    try:
        async with tenant_session(tenant_id) as session:
            await _prepare_readonly(session)
            ward_rows = await session.execute(
                text(
                    "SELECT DISTINCT ward_name FROM beds "
                    "WHERE ward_name IS NOT NULL LIMIT 100"
                )
            )
            wards = [str(r[0]) for r in ward_rows.fetchall() if r[0]]
            drug_rows = await session.execute(
                text(
                    "SELECT DISTINCT drug_name FROM drug_inventory "
                    "WHERE drug_name IS NOT NULL AND is_active LIMIT 500"
                )
            )
            drugs = [str(r[0]) for r in drug_rows.fetchall() if r[0]]
    except Exception:
        logger.warning("could not load known ward or drug names for routing")
    return wards, drugs


async def execute(
    tenant_id: str,
    routed: list[RoutedMetric],
    aliases: AliasTable | None = None,
) -> list[MetricResult]:
    """Run the selected metrics against the tenant database.

    A session is opened only because at least one metric matched. Every failure
    mode - an unresolvable tenant DSN, an unreachable database, a timeout -
    returns failed results rather than raising, so the caller can still answer
    from the content pack.

    `aliases` is the caller's per-request label table. It is required only for a
    patient-tier metric, which fails closed without it rather than answering
    with a name in it.
    """
    if not tenant_id or not routed:
        return []

    results: list[MetricResult] = []
    pending: list[RoutedMetric] = []

    for item in routed:
        # A patient-tier result is never cached. Its labels are issued for one
        # request and mean nothing in the next, so a cached row would carry
        # PATIENT_1 into a request whose alias table has never heard of it - and
        # rehydration would reject the whole answer. Keeping these rows out of a
        # process-wide cache also means the one thing in the catalog that is
        # about a person is never held anywhere after its request ends.
        if item.definition.tier is MetricTier.PATIENT:
            pending.append(item)
            continue
        binds = item.params.as_binds(item.definition.params)
        cached = _CACHE.get(_cache_key(tenant_id, item.definition, binds))
        if cached is not None:
            results.append(cached)
        else:
            pending.append(item)

    if not pending:
        return results

    timeout = float(getattr(settings, "assistant_live_data_timeout_seconds", 3.0))
    try:
        async with tenant_session(tenant_id) as session:
            await _prepare_readonly(session)
            for item in pending:
                try:
                    outcome = await asyncio.wait_for(
                        _run_one(
                            session, item.definition, item.params, aliases=aliases
                        ),
                        timeout=timeout + 1,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    outcome = MetricResult(
                        metric_id=item.definition.metric_id,
                        label=item.definition.label,
                        failed=True,
                    )
                if not outcome.failed and item.definition.tier is not MetricTier.PATIENT:
                    binds = item.params.as_binds(item.definition.params)
                    _CACHE[_cache_key(tenant_id, item.definition, binds)] = outcome
                results.append(outcome)
    except Exception:
        logger.warning("live data unavailable for tenant, answering from content only")
        results.extend(
            MetricResult(
                metric_id=item.definition.metric_id,
                label=item.definition.label,
                failed=True,
            )
            for item in pending
        )

    return results
