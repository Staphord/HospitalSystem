from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.contracts import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantConversationListResponse,
    AssistantConversationResponse,
    AssistantErrorCode,
    AssistantErrorResponse,
    AssistantFeedbackRequest,
    AssistantSuggestion,
    AssistantSuggestionsResponse,
    AssistantVoiceTranscriptResponse,
)
from app.assistant.service import (
    answer_question,
    build_caller,
    clear_conversations,
    delete_conversation,
    get_conversation,
    list_conversations,
    record_feedback,
    transcribe_capture,
)
from app.assistant.flags import AssistantCapability, is_capability_enabled
from app.assistant.retrieval import build_retrieval_context
from app.assistant.suggestions import build_suggestions
from app.core.config import settings
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.tenant import tenant_session

logger = logging.getLogger("assistant.router")

router = APIRouter(tags=["Assistant"])

# Mounted under the existing /api/v1/reports prefix, so the assistant reaches the
# browser through the gateway route that already exists for report-service. No
# new gateway route and no per-service frontend base URL is introduced.

# Hard ceiling on an upload, taken from configuration so an operator can lower
# it. It sits below the gateway body limit, so an oversized capture is refused
# here with an assistant error rather than by shared middleware.
MAX_UPLOAD_BYTES = int(getattr(settings, "assistant_max_audio_bytes", 5 * 1024 * 1024))

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
    AssistantErrorCode.INVALID_AUDIO: 400,
    AssistantErrorCode.AUDIO_TOO_LONG: 400,
    AssistantErrorCode.UNSUPPORTED_AUDIO_FORMAT: 415,
}


def _request_id(request: Request) -> str:
    """Reuse the request id minted by AuditLogMiddleware.

    A second identifier would make an assistant answer impossible to line up
    with the HTTP audit row for the same request.
    """
    existing = getattr(request.state, "request_id", None)
    return str(existing) if existing else str(uuid.uuid4())


async def get_history_db(ctx: TenantContext = Depends(get_current_tenant)):
    """Yield a tenant session for chat history, or None.

    Chat history is the only part of the assistant that touches a database, and
    it is independently switched. So this dependency opens nothing at all unless
    history is on: with the flag off, report-service answers questions exactly
    as it did before, with no tenant connection involved.

    When history is on but the tenant database cannot be reached, this yields
    None rather than raising. Answering a question must not start failing
    because the store behind the history panel is down; the chat route treats a
    missing session as "do not store this one", and the history routes report
    that history is unavailable.
    """
    if not is_capability_enabled(AssistantCapability.CHAT_HISTORY) or not ctx.tenant_id:
        yield None
        return

    try:
        opened = tenant_session(ctx.tenant_id)
        session = await opened.__aenter__()
    except Exception:
        # No DSN, or no connection. Logged without the question in it.
        logger.warning("assistant history session unavailable")
        yield None
        return

    try:
        yield session
    finally:
        await opened.__aexit__(None, None, None)


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
    db: AsyncSession | None = Depends(get_history_db),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    result, audit = await answer_question(request_id, caller, payload, db)

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


# Starting questions
#
# What the panel offers a caller who has opened the assistant and typed nothing.
# It is computed here rather than hardcoded in the browser because only the
# server knows which content this caller may read and which figures their roles
# reach. The panel previously shipped three fixed questions, two of which matched
# no content at all and one of which only worked for reception, so most users
# were invited to ask something that could not be answered for them.


@router.get(
    "/assistant/suggestions",
    response_model=AssistantSuggestionsResponse,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Starting questions this caller can actually get an answer to",
)
@limiter.limit("60/minute")
async def assistant_suggestions(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    # The same capability gate the chat itself applies. With the assistant
    # switched off for this deployment the feature is absent, not forbidden.
    if not is_capability_enabled(AssistantCapability.OPERATIONAL_CHAT):
        return AssistantSuggestionsResponse(request_id=request_id, suggestions=[])

    # A read-only session may ask nothing, so it is offered nothing. Everything
    # below is derived from the verified token; nothing is accepted from the
    # request.
    if caller.scope == "readonly":
        return AssistantSuggestionsResponse(request_id=request_id, suggestions=[])

    context = build_retrieval_context(caller.tenant_id, caller.roles)
    built = build_suggestions(
        context, caller.roles, is_super_admin=caller.is_super_admin
    )
    return AssistantSuggestionsResponse(
        request_id=request_id,
        suggestions=[
            AssistantSuggestion(question=s.question, kind=s.kind) for s in built
        ],
    )


# Chat history
#
# Read and delete only. A conversation is created by asking a question, never by
# a browser posting one, so there is no endpoint here that accepts conversation
# text. Each route resolves the conversation against the caller's own rows, so
# an id from another user's browser is answered as not available.


@router.get(
    "/assistant/conversations",
    response_model=AssistantConversationListResponse,
    responses={403: {"model": AssistantErrorResponse}},
    summary="List your own previous assistant conversations",
)
@limiter.limit("60/minute")
async def assistant_list_conversations(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession | None = Depends(get_history_db),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    result, audit = await list_conversations(request_id, caller, db)
    request.state.assistant_audit = audit

    if isinstance(result, AssistantErrorResponse):
        return _error_response(result)
    return result


@router.get(
    "/assistant/conversations/{conversation_id}",
    response_model=AssistantConversationResponse,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Reopen one of your own previous conversations",
)
@limiter.limit("60/minute")
async def assistant_get_conversation(
    request: Request,
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession | None = Depends(get_history_db),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    result, audit = await get_conversation(request_id, caller, conversation_id, db)
    request.state.assistant_audit = audit

    if isinstance(result, AssistantErrorResponse):
        return _error_response(result)
    return result


@router.delete(
    "/assistant/conversations/{conversation_id}",
    status_code=204,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Delete one of your own previous conversations",
)
@limiter.limit("30/minute")
async def assistant_delete_conversation(
    request: Request,
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession | None = Depends(get_history_db),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    error, audit = await delete_conversation(request_id, caller, conversation_id, db)
    request.state.assistant_audit = audit

    if error is not None:
        return _error_response(error)
    return Response(status_code=204)


@router.delete(
    "/assistant/conversations",
    status_code=204,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Delete all of your own assistant history",
)
@limiter.limit("10/minute")
async def assistant_clear_conversations(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession | None = Depends(get_history_db),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    error, audit = await clear_conversations(request_id, caller, db)
    request.state.assistant_audit = audit

    if error is not None:
        return _error_response(error)
    return Response(status_code=204)


@router.post(
    "/assistant/voice/transcribe",
    response_model=AssistantVoiceTranscriptResponse,
    responses={403: {"model": AssistantErrorResponse}},
    summary="Transcribe one push-to-talk recording for the speaker to confirm",
)
@limiter.limit("10/minute")
async def assistant_voice_transcribe(
    request: Request,
    language: str | None = Query(
        default=None,
        max_length=5,
        description=(
            "Optional recognition hint, en or sw. Anything else is ignored and "
            "the language is detected instead."
        ),
    ),
    ctx: TenantContext = Depends(get_current_tenant),
):
    request_id = _request_id(request)
    caller = build_caller(ctx)

    # The declared length is refused before the body is read, so an oversized
    # upload is not buffered into memory first. The real length is checked again
    # after reading, because a declared length is only a claim.
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_UPLOAD_BYTES:
                return _error_response(
                    AssistantErrorResponse(
                        request_id=request_id,
                        code=AssistantErrorCode.REQUEST_TOO_LARGE,
                        message="That recording is too large.",
                    )
                )
        except ValueError:
            return _error_response(
                AssistantErrorResponse(
                    request_id=request_id,
                    code=AssistantErrorCode.INVALID_AUDIO,
                    message="That recording could not be read.",
                )
            )

    audio = await request.body()

    result, audit = await transcribe_capture(
        request_id,
        caller,
        audio,
        request.headers.get("content-type"),
        language=language,
    )

    request.state.assistant_audit = audit

    if isinstance(result, AssistantErrorResponse):
        return _error_response(result)
    return result
