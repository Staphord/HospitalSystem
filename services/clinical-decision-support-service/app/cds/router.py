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
    CdsErrorCode,
    CdsErrorResponse,
    DifferentialFeedbackRequest,
    DifferentialFeedbackResponse,
    DifferentialRequest,
    DifferentialResponse,
)
from app.cds.service import (
    build_caller,
    differential_support,
    record_differential_feedback,
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
    CdsErrorCode.SUGGESTION_UNAVAILABLE: 503,
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


@router.post(
    "/differential/suggest",
    response_model=DifferentialResponse,
    responses={403: {"model": CdsErrorResponse}},
    tags=["Clinical differential support"],
    summary="Diagnosis suggestions for clinician review",
)
@limiter.limit("10/minute")
async def differential_suggest(
    request: Request,
    payload: DifferentialRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await differential_support(_request_id(request), build_caller(ctx), payload, db)
    if isinstance(result, CdsErrorResponse):
        return _error_response(result)
    return result


@router.post(
    "/differential/feedback",
    response_model=DifferentialFeedbackResponse,
    responses={403: {"model": CdsErrorResponse}},
    tags=["Clinical differential support"],
    summary="Record a clinician's judgement of one suggestion",
)
@limiter.limit("30/minute")
async def differential_feedback(
    request: Request,
    payload: DifferentialFeedbackRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await record_differential_feedback(
        _request_id(request), build_caller(ctx), payload, db
    )
    if isinstance(result, CdsErrorResponse):
        return _error_response(result)
    return result
