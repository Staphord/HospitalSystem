"""Orchestration for the clinical decision support endpoints.

The order of the gates is deliberate and is the same on every path:

  1. the service kill switch,
  2. the capability kill switch,
  3. the caller's clinical role, from the verified token,
  4. the caller's access to the resource, checked server-side,
  5. only then, any data loading or evaluation.

Nothing before step 5 touches patient data, so a caller who fails any gate never
causes a clinical record to be read at all. The tenant is never one of the
arguments: it arrives with the database session, resolved from the token.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.cds import audit, metrics
from app.cds.access import load_differential_context
from app.cds.contracts import (
    CdsErrorCode,
    CdsErrorResponse,
    DifferentialFeedbackRequest,
    DifferentialFeedbackResponse,
    DifferentialRequest,
    DifferentialResponse,
)
from app.cds.differential import run_differential
from app.cds.flags import CdsCapability, is_capability_enabled
from app.cds.permissions import clinical_role_of, is_role_allowed

logger = logging.getLogger("cds.service")


class Caller:
    """The authenticated actor, built only from verified token claims."""

    __slots__ = ("tenant_id", "actor_sub", "roles", "is_super_admin", "clinical_role")

    def __init__(self, ctx) -> None:
        self.tenant_id: str = getattr(ctx, "tenant_id", None) or ""
        self.actor_sub: str = getattr(ctx, "user_sub", None) or ""
        self.roles: list[str] = list(getattr(ctx, "roles", None) or [])
        self.is_super_admin: bool = bool(getattr(ctx, "is_super_admin", False))
        self.clinical_role: str = clinical_role_of(self.roles)


def build_caller(ctx) -> Caller:
    return Caller(ctx)


def guard(request_id: str, caller: Caller, capability: CdsCapability) -> CdsErrorResponse | None:
    """Apply the kill switches and the role check, in that order."""
    if not is_capability_enabled(capability):
        # Switched off means the feature is not there for this caller, not that
        # they were refused something that exists.
        metrics.record("capability.disabled_request")
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.CAPABILITY_DISABLED,
            message="This feature is not available.",
        )

    if not is_role_allowed(capability, caller.roles, caller.is_super_admin):
        metrics.record("authorization.denied")
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.PERMISSION_DENIED,
            message="Your role does not have access to clinical decision support.",
        )

    if not caller.tenant_id:
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.PERMISSION_DENIED,
            message="Your account is not associated with a hospital.",
        )

    return None


async def differential_support(
    request_id: str,
    caller: Caller,
    payload: DifferentialRequest,
    db: AsyncSession,
) -> DifferentialResponse | CdsErrorResponse:
    """Produce considerations for clinician review, and record that it happened."""
    response = await run_differential(request_id, caller, payload, db, guard)
    if isinstance(response, CdsErrorResponse):
        return response

    metrics.record("differential.requested")
    metrics.record(f"differential.{response.status.value}")
    for _ in response.red_flags:
        metrics.record("differential.red_flag_raised")

    audit.log_suggestion(
        request_id=request_id,
        suggestion_id=response.suggestion_id,
        tenant_id=caller.tenant_id,
        actor_sub=caller.actor_sub,
        actor_role=caller.clinical_role,
        status=response.status.value,
        red_flag_rule_ids=[flag.rule_id for flag in response.red_flags],
        prompt_version=response.prompt_version,
        model_version=response.model_version,
    )

    try:
        # The patient id is read again here rather than carried on the response,
        # which deliberately does not expose one to the browser.
        context = await load_differential_context(db, payload.visit_id, 1)
        await audit.persist_suggestion(
            db,
            request_id=request_id,
            tenant_id=caller.tenant_id,
            actor_sub=caller.actor_sub,
            actor_role=caller.clinical_role,
            patient_id=context.patient_id,
            response=response,
        )
    except Exception:
        logger.exception("cds differential suggestion record could not be persisted")

    return response


async def record_differential_feedback(
    request_id: str,
    caller: Caller,
    payload: DifferentialFeedbackRequest,
    db: AsyncSession,
) -> DifferentialFeedbackResponse | CdsErrorResponse:
    """Record a clinician's judgement of one suggestion.

    Written for humans to review. Nothing reads it back into the workflow, so a
    rating can never quietly change what the next clinician is shown.
    """
    error = guard(request_id, caller, CdsCapability.DIFFERENTIAL_SUPPORT)
    if error is not None:
        return error

    suggestion = await audit.find_suggestion(db, payload.suggestion_id, caller.tenant_id)
    if suggestion is None:
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.RESOURCE_NOT_FOUND,
            message="That suggestion is not available.",
        )

    _, recorded_at = await audit.persist_feedback(
        db,
        request_id=request_id,
        suggestion_id=payload.suggestion_id,
        tenant_id=caller.tenant_id,
        actor_sub=caller.actor_sub,
        actor_role=caller.clinical_role,
        rating=payload.rating,
        comment=(payload.comment or None),
    )

    metrics.record("differential.feedback")

    return DifferentialFeedbackResponse(
        request_id=request_id,
        suggestion_id=payload.suggestion_id,
        recorded_at=recorded_at,
    )
