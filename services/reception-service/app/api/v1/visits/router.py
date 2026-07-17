from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.api.v1.schemas import (
    InsurancePolicyResponse,
    InsuranceVerifyRequest,
    QueueEntryWithContext,
    QueueTodayResponse,
    VisitCreateRequest,
    VisitCreateResponse,
    VisitDetailResponse,
)
from app.services.orchestrator import (
    create_visit,
    get_visit_detail,
    get_reception_queue,
    triage_queue_today,
    verify_insurance_policy,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Group 3 — Visit Creation
# ---------------------------------------------------------------------------

@router.post(
    "/visits",
    response_model=VisitCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a visit (FR-06 / 3.1)",
)
@limiter.limit("10/minute")
async def create(
    body: VisitCreateRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Create a visit and auto-assign to the triage queue.

    For insurance payments, `insurance_id` must reference an active policy
    belonging to this patient. Returns the created visit, full queue entry,
    and any `verification_flag` if the policy is not yet verified.
    """
    return await create_visit(body, request)


@router.get(
    "/visits/{visit_id}",
    response_model=VisitDetailResponse,
    summary="Get visit detail (3.2)",
)
@limiter.limit("30/minute")
async def get_visit(
    visit_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Get full visit detail with nested patient and insurance summaries."""
    return await get_visit_detail(visit_id, request)


# ---------------------------------------------------------------------------
# Group 2 — Insurance verification (separate route from patient namespace)
# ---------------------------------------------------------------------------

@router.patch(
    "/insurance/{insurance_id}/verify",
    response_model=InsurancePolicyResponse,
    summary="Verify insurance policy (FR-05 / 2.2)",
)
@limiter.limit("10/minute")
async def verify_insurance(
    insurance_id: str,
    body: InsuranceVerifyRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Record the manual verification outcome for an insurance policy.

    `verification_status` must be `verified` or `rejected`.
    Sets `verified_at` to the current timestamp on the policy record.
    """
    return await verify_insurance_policy(insurance_id, body, request)


# ---------------------------------------------------------------------------
# Group 4 — Reception Queue View
# ---------------------------------------------------------------------------

@router.get(
    "/queue",
    response_model=list[QueueEntryWithContext],
    summary="Reception worklist — queue with patient context (FR-07 / 4.1)",
)
@limiter.limit("30/minute")
async def reception_queue(
    request: Request,
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by queue status: waiting / in_progress / completed / skipped",
    ),
    queue_type: str = Query(
        "triage",
        description="Queue section to view: triage / doctor / lab / radiology / pharmacy / billing",
    ),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Return the reception worklist — queue entries enriched with patient and visit context.

    Concurrently fetches patient profile and visit summary for each queue entry.
    """
    return await get_reception_queue(request, queue_status=status_filter, queue_type=queue_type)


# ---------------------------------------------------------------------------
# Legacy triage queue endpoint (kept for backward compat)
# ---------------------------------------------------------------------------

@router.get(
    "/visits/queues/triage/today",
    response_model=list[QueueTodayResponse],
    include_in_schema=False,
)
@limiter.limit("30/minute")
async def triage_today(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await triage_queue_today(request)
