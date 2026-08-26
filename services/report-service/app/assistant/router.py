from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.assistant.contracts import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantErrorCode,
    AssistantErrorResponse,
    AssistantFeedbackRequest,
)
from app.assistant.service import answer_question, build_caller, record_feedback
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant

router = APIRouter(tags=["Assistant"])

# Mounted under the existing /api/v1/reports prefix, so the assistant reaches the
# browser through the gateway route that already exists for report-service. No
# new gateway route and no per-service frontend base URL is introduced.

_STATUS_BY_CODE: dict[AssistantErrorCode, int] = {
    # The capability is switched off for this deployment, so for this caller the
    # feature is simply not there.
    AssistantErrorCode.CAPABILITY_DISABLED: 404,
    AssistantErrorCode.PERMISSION_DENIED: 403,
    AssistantErrorCode.INVALID_REQUEST: 400,
    AssistantErrorCode.REQUEST_TOO_LARGE: 413,
    AssistantErrorCode.PROVIDER_TIMEOUT: 504,
    AssistantErrorCode.PROVIDER_UNAVAILABLE: 503,
    AssistantErrorCode.INVALID_PROVIDER_OUTPUT: 502,
    AssistantErrorCode.UNSUPPORTED_QUESTION: 200,
    AssistantErrorCode.NEEDS_REVIEW: 200,
}


def _request_id(request: Request) -> str:
    """Reuse the request id minted by AuditLogMiddleware.

    A second identifier would make an assistant answer impossible to line up
    with the HTTP audit row for the same request.
    """
    existing = getattr(request.state, "request_id", None)
    return str(existing) if existing else str(uuid.uuid4())


def _error_response(error: AssistantErrorResponse) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS_BY_CODE.get(error.code, 400),
        content=error.model_dump(mode="json"),
    )


@router.post(
    "/assistant/chat",
    response_model=AssistantChatResponse,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Ask an operational question about using the hospital system",
)
@limiter.limit("20/minute")
async def assistant_chat(
    request: Request,
    payload: AssistantChatRequest,
    ctx: TenantContext = Depends(get_current_tenant),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    result, audit = await answer_question(request_id, caller, payload)

    # Carried on request state so later phases can persist it alongside the
    # existing audit row without changing this endpoint.
    request.state.assistant_audit = audit

    if isinstance(result, AssistantErrorResponse):
        return _error_response(result)
    return result


@router.post(
    "/assistant/feedback",
    status_code=204,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Give feedback on a previous assistant answer",
)
@limiter.limit("30/minute")
async def assistant_feedback(
    request: Request,
    payload: AssistantFeedbackRequest,
    ctx: TenantContext = Depends(get_current_tenant),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    error, audit = record_feedback(request_id, caller, payload)
    request.state.assistant_audit = audit

    if error is not None:
        return _error_response(error)
    return Response(status_code=204)
