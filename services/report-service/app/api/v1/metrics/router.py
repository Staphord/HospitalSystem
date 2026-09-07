from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.assistant.flags import AssistantCapability, is_capability_enabled
from app.assistant.live import registry as live_registry
from app.assistant.live.aliases import AliasTable
from app.assistant.live.catalog.flow import QUEUE_TYPE_SYNONYMS
from app.assistant.live.contracts import MetricParams
from app.assistant.live.execution import execute, format_value
from app.assistant.live.registry import get_metric, permitted_metrics
from app.assistant.live.routing import (
    MAX_WINDOW_DAYS,
    PATIENT_NUMBER_PATTERN,
    VISIT_NUMBER_PATTERN,
    RoutedMetric,
)
from app.assistant.permissions import normalize_roles
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant

# The operational figures the assistant reads, exposed directly.
#
# These endpoints and the assistant share one registry, one role gate and one
# projection, so a figure cannot be reachable here on terms it is not reachable
# there. The assistant calls the execution functions in process rather than
# calling these routes: a round trip from the service to itself would add
# latency and a failure mode without changing what is returned.
#
# They exist because a metric is far easier to verify against the database when
# it can be requested on its own, and because the dashboards need exactly these
# numbers. report-service has carried an empty reports placeholder since the
# split; this is the beginning of filling it.

router = APIRouter(tags=["Metrics"])

live_registry.load_catalog()

# The queue_type enum, as the tenant schema declares it. Taken from the catalog's
# synonym map so the two cannot drift apart.
VALID_QUEUE_TYPES: frozenset[str] = frozenset(QUEUE_TYPE_SYNONYMS.values())


def _require_capability() -> None:
    """Live data is off by default and switched on per deployment."""
    if not is_capability_enabled(AssistantCapability.LIVE_DATA):
        # Off means absent, not forbidden: for this deployment the feature is
        # simply not there, which is the same stance the assistant routes take.
        raise HTTPException(status_code=404, detail="Live metrics are not enabled.")


def _caller_roles(ctx: TenantContext) -> frozenset[str]:
    return frozenset(normalize_roles(ctx.roles or []))


@router.get(
    "/metrics",
    summary="List the operational figures this caller may read",
)
@limiter.limit("60/minute")
async def list_metrics(
    request: Request, ctx: TenantContext = Depends(get_current_tenant)
) -> dict[str, Any]:
    """List permitted metrics. Never lists one the caller could not run."""
    _require_capability()
    available = permitted_metrics(_caller_roles(ctx), is_super_admin=ctx.is_super_admin)
    return {
        "metrics": [
            {
                "metric_id": definition.metric_id,
                "label": definition.label,
                "tier": definition.tier.value,
                "fields": sorted(definition.exposed_fields),
                "parameters": sorted(definition.params),
            }
            for definition in available
        ]
    }


@router.get(
    "/metrics/{metric_id}",
    summary="Read one operational figure",
)
@limiter.limit("60/minute")
async def read_metric(
    request: Request,
    metric_id: str,
    ward_name: str | None = Query(default=None, max_length=120),
    drug_name: str | None = Query(default=None, max_length=200),
    queue_type: str | None = Query(default=None, max_length=40),
    # Constrained to the formats patient-service and visit-service generate, so
    # a value that could never identify anybody is refused here rather than
    # becoming a query that matches nothing and reads like an empty answer.
    patient_number: str | None = Query(
        default=None, max_length=32, pattern="^" + PATIENT_NUMBER_PATTERN + "$"
    ),
    visit_number: str | None = Query(
        default=None, max_length=32, pattern="^" + VISIT_NUMBER_PATTERN + "$"
    ),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    ctx: TenantContext = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Run one metric for the caller's own tenant.

    The tenant is taken from the verified token and is never accepted as a
    parameter, so this cannot be pointed at another hospital.

    A patient-tier metric answers here with the same opaque label the model is
    given, not a name. Rehydration belongs to the assistant path, which is the
    one that has an answer to put a name back into; keeping it out of here means
    there is exactly one place in the service where a patient's name is written
    into text.
    """
    _require_capability()

    definition = get_metric(metric_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="No such metric.")

    roles = _caller_roles(ctx)
    if not definition.is_permitted(roles, is_super_admin=ctx.is_super_admin):
        # Deny by default, and with the same message a missing metric gives, so
        # this cannot be used to enumerate which figures exist.
        raise HTTPException(status_code=403, detail="You may not read that figure.")

    if not ctx.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant resolved for this caller.")
    if ctx.scope == "readonly":
        raise HTTPException(
            status_code=403, detail="A read-only session may not read live figures."
        )

    today = date.today()
    resolved_end = end or today
    resolved_start = start or resolved_end
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="'start' must be on or before 'end'.")
    if (resolved_end - resolved_start).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=400, detail="The date range cannot exceed 12 months."
        )

    if queue_type is not None and queue_type not in VALID_QUEUE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unknown queue. Valid queues: " + ", ".join(sorted(VALID_QUEUE_TYPES)),
        )

    params = MetricParams(
        start=resolved_start,
        end=resolved_end,
        ward_name=ward_name,
        drug_name=drug_name,
        queue_type=queue_type,
        patient_number=patient_number,
        visit_number=visit_number,
        actor_sub=ctx.user_sub,
    )

    # Refuse rather than bind a missing parameter to NULL.
    #
    # A metric filtering on `queue_type = NULL` matches no row and returns a
    # confident zero, which is indistinguishable from a genuinely empty queue.
    # This caught exactly that: the endpoint once omitted queue_type entirely and
    # reported an empty doctor queue while twenty-seven patients were being seen.
    # Any metric parameter that is declared but not supplied is an error here.
    missing = sorted(
        name for name, value in params.as_binds(definition.params).items() if value is None
    )
    if missing:
        raise HTTPException(
            status_code=400,
            detail="That figure needs: " + ", ".join(missing),
        )
    results = await execute(
        ctx.tenant_id,
        [RoutedMetric(definition=definition, params=params, score=1)],
        # One table per request here too. A patient-tier metric fails closed
        # without one rather than returning a row with a name in it.
        aliases=AliasTable(),
    )
    if not results:
        raise HTTPException(status_code=503, detail="That figure is not available now.")

    outcome = results[0]
    if outcome.failed:
        # No database error, DSN, or stack trace reaches a reader here either.
        raise HTTPException(status_code=503, detail="That figure could not be read.")

    return {
        "metric_id": outcome.metric_id,
        "label": outcome.label,
        "read_at": outcome.read_at.isoformat() if outcome.read_at else None,
        "rows": [
            {name: format_value(value) for name, value in row.values.items()}
            for row in outcome.rows
        ],
    }
