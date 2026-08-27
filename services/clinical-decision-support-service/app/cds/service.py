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
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.cds import audit
from app.cds.access import VisitAccessError, load_terminology, load_visit_context
from app.cds.contracts import (
    ActiveRulesetResponse,
    AlertAction,
    AlertActionRequest,
    AlertActionResponse,
    AlertStatus,
    CdsErrorCode,
    CdsErrorResponse,
    MedicationCheckRequest,
    MedicationCheckResponse,
    MedicineInput,
    NormalizedMedicine,
    NormalizeRequest,
    NormalizeResponse,
    ReviewReason,
)
from app.cds.engine import evaluate
from app.cds.flags import CdsCapability, is_capability_enabled
from app.cds.permissions import clinical_role_of, is_role_allowed, may_override
from app.cds.rules import load_active_ruleset
from app.core.config import settings

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
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.CAPABILITY_DISABLED,
            message="This feature is not available.",
        )

    if not is_role_allowed(capability, caller.roles, caller.is_super_admin):
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


def active_ruleset(request_id: str) -> ActiveRulesetResponse:
    """Report which approved ruleset is answering, for reproducibility."""
    load = load_active_ruleset()
    if load.ruleset is None:
        return ActiveRulesetResponse(
            request_id=request_id,
            available=False,
            unavailable_reason=load.reason or ReviewReason.NO_APPROVED_RULESET,
        )
    return ActiveRulesetResponse(
        request_id=request_id,
        available=True,
        descriptor=load.ruleset.descriptor,
        unavailable_reason=load.reason,
    )


async def normalize_medicines(
    request_id: str,
    caller: Caller,
    payload: NormalizeRequest,
    db: AsyncSession,
) -> NormalizeResponse | CdsErrorResponse:
    """Resolve typed names to products so a clinician can confirm them."""
    error = guard(request_id, caller, CdsCapability.MEDICATION_CHECK)
    if error is not None:
        return error

    terminology = await load_terminology(db)
    return NormalizeResponse(
        request_id=request_id,
        source=terminology.name,
        results=[terminology.normalize(medicine) for medicine in payload.medicines],
        evaluated_at=datetime.now(timezone.utc),
    )


async def run_medication_check(
    request_id: str,
    caller: Caller,
    payload: MedicationCheckRequest,
    db: AsyncSession,
) -> MedicationCheckResponse | CdsErrorResponse:
    """Run the deterministic medication check for one visit."""
    error = guard(request_id, caller, CdsCapability.MEDICATION_CHECK)
    if error is not None:
        return error

    max_medicines = int(getattr(settings, "cds_max_medications_per_check", 30))

    try:
        context = await load_visit_context(db, payload.visit_id, max_medicines)
    except VisitAccessError:
        # Identical answer for "does not exist" and "not yours", so the endpoint
        # cannot be used to probe for visit ids in another hospital.
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.RESOURCE_NOT_FOUND,
            message="That visit is not available.",
        )
    except Exception:
        logger.exception("cds visit context load failed")
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.CHECK_UNAVAILABLE,
            message="The medication check could not be completed. Refer for manual review.",
        )

    medicines: list[MedicineInput] = []
    if payload.include_prescribed:
        medicines.extend(context.prescribed)
    medicines.extend(payload.additional_medicines)

    if len(medicines) > max_medicines:
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.TOO_MANY_MEDICINES,
            message="Too many medicines were submitted for one check.",
        )

    terminology = await load_terminology(db)
    normalized: list[NormalizedMedicine] = [
        terminology.to_normalized(medicine) for medicine in medicines
    ]

    outcome = evaluate(
        medicines=normalized,
        allergies=context.allergies,
        ruleset_load=load_active_ruleset(),
    )

    response = MedicationCheckResponse(
        request_id=request_id,
        check_id=uuid4(),
        visit_id=payload.visit_id,
        status=outcome.status,
        findings=list(outcome.findings),
        medicines=normalized,
        review_reasons=list(outcome.review_reasons),
        ruleset=outcome.ruleset,
        checks_performed=list(outcome.checks_performed),
        checks_not_performed=list(outcome.checks_not_performed),
        limitations=list(outcome.limitations),
        requires_human_review=True,
        evaluated_at=datetime.now(timezone.utc),
    )

    audit.log_check(
        audit.build_log_record(
            request_id=request_id,
            check_id=response.check_id,
            tenant_id=caller.tenant_id,
            actor_sub=caller.actor_sub,
            actor_role=caller.clinical_role,
            visit_id=response.visit_id,
            response=response,
        )
    )

    try:
        await audit.persist_check(
            db,
            request_id=request_id,
            tenant_id=caller.tenant_id,
            actor_sub=caller.actor_sub,
            actor_role=caller.clinical_role,
            patient_id=context.patient_id,
            response=response,
        )
    except Exception:
        # The clinician still sees the findings. Losing the audit row is an
        # operational fault to alarm on, never a reason to swallow an alert.
        logger.exception("cds check audit record could not be persisted")

    return response


async def record_alert_action(
    request_id: str,
    caller: Caller,
    payload: AlertActionRequest,
    db: AsyncSession,
) -> AlertActionResponse | CdsErrorResponse:
    """Record that a clinician acknowledged a finding, or overrode an alert."""
    error = guard(request_id, caller, CdsCapability.MEDICATION_CHECK)
    if error is not None:
        return error

    if payload.action is AlertAction.OVERRIDE and not may_override(
        caller.roles, caller.is_super_admin
    ):
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.PERMISSION_DENIED,
            message="Your role may not override a medication alert.",
        )

    check = await audit.find_check(db, payload.check_id, caller.tenant_id)
    if check is None:
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.RESOURCE_NOT_FOUND,
            message="That check is not available.",
        )

    entry = (check.finding_index or {}).get(payload.finding_id)
    if not isinstance(entry, dict):
        # The finding did not come from this check. Accepting it would let an
        # acknowledgement be recorded against something nobody was ever shown.
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.RESOURCE_NOT_FOUND,
            message="That finding is not part of this check.",
        )

    if (
        payload.action is AlertAction.OVERRIDE
        and entry.get("status") != AlertStatus.ALERT.value
    ):
        # A needs_review finding is an open question, not a decision to be
        # overruled. It has to be reviewed, not dismissed.
        return CdsErrorResponse(
            request_id=request_id,
            code=CdsErrorCode.INVALID_REQUEST,
            message="Only a decided alert can be overridden. This finding requires review.",
        )

    action_id, recorded_at = await audit.persist_action(
        db,
        request_id=request_id,
        check_id=payload.check_id,
        finding_id=payload.finding_id,
        action=payload.action,
        tenant_id=caller.tenant_id,
        actor_sub=caller.actor_sub,
        actor_role=caller.clinical_role,
        reason=(payload.reason or None),
    )

    audit.log_action(
        request_id=request_id,
        action_id=action_id,
        check_id=payload.check_id,
        tenant_id=caller.tenant_id,
        actor_sub=caller.actor_sub,
        actor_role=caller.clinical_role,
        action=payload.action,
    )

    return AlertActionResponse(
        request_id=request_id,
        action_id=action_id,
        check_id=payload.check_id,
        finding_id=payload.finding_id,
        action=payload.action,
        recorded_at=recorded_at,
    )


def check_id_from(value: str) -> UUID | None:
    """Parse a check id without leaking a parser error to the caller."""
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
