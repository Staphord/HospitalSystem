"""Audit for clinical decision support.

Two destinations, on purpose.

The tenant database gets the durable, append-only record: which actor, acting
in which clinical role, ran which check against which visit, what the ruleset
was, and what they acknowledged or overrode. That belongs with the hospital's
own clinical record.

The application log gets identifiers, versions, and counts only. No patient
identifier, no drug name, no finding text, no override reason, no credential,
no stack trace. An engineer reading the log can tell that a check ran, what it
concluded, and which ruleset answered — and nothing about who it was for.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.cds.contracts import (
    AlertAction,
    AlertStatus,
    MedicationCheckResponse,
)
from app.models.cds import CdsAlertAction, CdsMedicationCheck

logger = logging.getLogger("cds.audit")

# Field names refused in an audit log line, by name rather than by inspection,
# so that adding one later is a deliberate act that fails a test first.
FORBIDDEN_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "patient_id",
        "patient_name",
        "patient_number",
        "date_of_birth",
        "allergies",
        "drug_name",
        "medicines",
        "explanation",
        "reason",
        "findings",
        "prompt",
        "api_key",
        "token",
        "database_url",
        "traceback",
    }
)

SAFE_LOG_FIELDS: tuple[str, ...] = (
    "request_id",
    "check_id",
    "tenant_id",
    "actor_sub",
    "actor_role",
    "visit_id",
    "status",
    "finding_count",
    "alert_count",
    "needs_review_count",
    "ruleset_source",
    "ruleset_version",
    "ruleset_stale",
)


def build_log_record(
    *,
    request_id: str,
    check_id: UUID,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    visit_id: UUID,
    response: MedicationCheckResponse,
) -> dict[str, object]:
    """Build the log line for one check, allowlisted field by field."""
    alerts = sum(1 for f in response.findings if f.status is AlertStatus.ALERT)
    needs_review = sum(1 for f in response.findings if f.status is AlertStatus.NEEDS_REVIEW)
    ruleset = response.ruleset

    record: dict[str, object] = {
        "request_id": request_id,
        "check_id": str(check_id),
        "tenant_id": tenant_id,
        "actor_sub": actor_sub,
        "actor_role": actor_role,
        "visit_id": str(visit_id),
        "status": response.status.value,
        "finding_count": len(response.findings),
        "alert_count": alerts,
        "needs_review_count": needs_review,
        "ruleset_source": ruleset.source_name if ruleset else None,
        "ruleset_version": ruleset.ruleset_version if ruleset else None,
        "ruleset_stale": ruleset.stale if ruleset else None,
    }
    return {key: record[key] for key in SAFE_LOG_FIELDS}


def log_check(record: dict[str, object]) -> None:
    logger.info(
        "cds medication check: "
        + " ".join(f"{key}={record.get(key)}" for key in SAFE_LOG_FIELDS)
    )


def log_action(
    *,
    request_id: str,
    action_id: UUID,
    check_id: UUID,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    action: AlertAction,
) -> None:
    """Log that an action happened. The reason text never reaches this line."""
    logger.info(
        "cds alert action: request_id=%s action_id=%s check_id=%s tenant_id=%s "
        "actor_sub=%s actor_role=%s action=%s",
        request_id,
        action_id,
        check_id,
        tenant_id,
        actor_sub,
        actor_role,
        action.value,
    )


async def persist_check(
    db: AsyncSession,
    *,
    request_id: str,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    patient_id: UUID | None,
    response: MedicationCheckResponse,
) -> None:
    """Append the check record. A failure here must not lose the clinical result.

    The clinician has already been shown the findings by the time this runs; a
    database problem is an operational fault to be alarmed on, not a reason to
    withhold a medication alert from the person holding the prescription.
    """
    ruleset = response.ruleset
    row = CdsMedicationCheck(
        finding_index={
            f.finding_id: {"status": f.status.value, "blocking": bool(f.blocking)}
            for f in response.findings
        },
        check_id=response.check_id,
        request_id=request_id,
        tenant_id=tenant_id,
        actor_sub=actor_sub,
        actor_role=actor_role,
        visit_id=response.visit_id,
        patient_id=patient_id,
        status=response.status.value,
        finding_count=len(response.findings),
        alert_count=sum(1 for f in response.findings if f.status is AlertStatus.ALERT),
        needs_review_count=sum(
            1 for f in response.findings if f.status is AlertStatus.NEEDS_REVIEW
        ),
        ruleset_source=ruleset.source_name if ruleset else None,
        ruleset_version=ruleset.ruleset_version if ruleset else None,
        ruleset_effective_date=ruleset.effective_date if ruleset else None,
        ruleset_stale=bool(ruleset.stale) if ruleset else False,
        evaluated_at=response.evaluated_at,
    )
    db.add(row)
    await db.commit()


async def find_check(db: AsyncSession, check_id: UUID, tenant_id: str) -> CdsMedicationCheck | None:
    """Look up a check, scoped to the tenant the token resolved to.

    The tenant is matched as well as the id, so a check id learned from
    somewhere else cannot be acknowledged from another hospital's session even
    if the databases were ever consolidated.
    """
    row = await db.get(CdsMedicationCheck, check_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def persist_action(
    db: AsyncSession,
    *,
    request_id: str,
    check_id: UUID,
    finding_id: str,
    action: AlertAction,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    reason: str | None,
) -> tuple[UUID, datetime]:
    """Append an acknowledgement or override and return its id and timestamp."""
    action_id = uuid4()
    recorded_at = datetime.now(timezone.utc)
    db.add(
        CdsAlertAction(
            action_id=action_id,
            check_id=check_id,
            finding_id=finding_id,
            action=action.value,
            tenant_id=tenant_id,
            actor_sub=actor_sub,
            actor_role=actor_role,
            request_id=request_id,
            reason=reason,
            created_at=recorded_at,
        )
    )
    await db.commit()
    return action_id, recorded_at
