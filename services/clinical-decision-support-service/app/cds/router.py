"""HTTP surface for clinical decision support.

Mounted at /api/v1/cds, which the API Gateway routes to this service. The
browser never reaches this service on its own port; it reaches the gateway,
which verifies the token and forwards the request.

Every handler is thin on purpose: parse, delegate, map the error code to a
status. All authorization, tenant resolution, and clinical logic happen behind
app.cds.service, so no route can accidentally skip a gate.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.cds.contracts import (
    ActiveRulesetResponse,
    AlertActionRequest,
    AlertActionResponse,
    CdsErrorCode,
    CdsErrorResponse,
    MedicationCheckRequest,
    MedicationCheckResponse,
    NormalizeRequest,
    NormalizeResponse,
)
from app.cds.flags import CdsCapability
from app.cds.service import (
    active_ruleset,
    build_caller,
    guard,
    normalize_medicines,
    record_alert_action,
    run_medication_check,
)
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db

router = APIRouter()

_STATUS_BY_CODE: dict[CdsErrorCode, int] = {
    # Switched off is indistinguishable from absent, so an operator pulling the
    # kill switch does not advertise that the capability exists.
    CdsErrorCode.CAPABILITY_DISABLED: 404,
    CdsErrorCode.PERMISSION_DENIED: 403,
    CdsErrorCode.INVALID_REQUEST: 400,
    CdsErrorCode.RESOURCE_NOT_FOUND: 404,
    CdsErrorCode.TOO_MANY_MEDICINES: 413,
    CdsErrorCode.CHECK_UNAVAILABLE: 503,
}


def _request_id(request: Request) -> str:
    """Reuse the request id the audit middleware settled on.

    That id is the gateway's own where the gateway supplied a well-formed one,
    so a clinician quoting the id from a screen and an engineer searching the
    gateway log are talking about the same request.
    """
    existing = getattr(request.state, "request_id", None)
    return str(existing) if existing else str(uuid.uuid4())


def _error_response(error: CdsErrorResponse) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS_BY_CODE.get(error.code, 400),
        content=error.model_dump(mode="json"),
    )


@router.get(
    "/rulesets/active",
    response_model=ActiveRulesetResponse,
    tags=["Ruleset"],
    summary="Which approved interaction ruleset is answering today",
)
@limiter.limit("30/minute")
async def get_active_ruleset(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
):
    request_id = _request_id(request)

    # Gated like every other route. When the kill switch is pulled the whole
    # surface has to be absent; one route still answering would tell a caller
    # the capability exists and is merely switched off.
    error = guard(request_id, build_caller(ctx), CdsCapability.MEDICATION_CHECK)
    if error is not None:
        return _error_response(error)

    return active_ruleset(request_id)


@router.post(
    "/medication/normalize",
    response_model=NormalizeResponse,
    responses={403: {"model": CdsErrorResponse}},
    tags=["Medication safety"],
    summary="Resolve typed medicine names to products for confirmation",
)
@limiter.limit("30/minute")
async def normalize(
    request: Request,
    payload: NormalizeRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await normalize_medicines(_request_id(request), build_caller(ctx), payload, db)
    if isinstance(result, CdsErrorResponse):
        return _error_response(result)
    return result


@router.post(
    "/medication/check",
    response_model=MedicationCheckResponse,
    responses={403: {"model": CdsErrorResponse}},
    tags=["Medication safety"],
    summary="Run the deterministic medication check for one visit",
)
@limiter.limit("20/minute")
async def medication_check(
    request: Request,
    payload: MedicationCheckRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await run_medication_check(_request_id(request), build_caller(ctx), payload, db)
    if isinstance(result, CdsErrorResponse):
        return _error_response(result)
    return result


@router.post(
    "/medication/alert-action",
    response_model=AlertActionResponse,
    responses={403: {"model": CdsErrorResponse}},
    tags=["Medication safety"],
    summary="Acknowledge a finding, or override a decided alert with a reason",
)
@limiter.limit("30/minute")
async def alert_action(
    request: Request,
    payload: AlertActionRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await record_alert_action(_request_id(request), build_caller(ctx), payload, db)
    if isinstance(result, CdsErrorResponse):
        return _error_response(result)
    return result
