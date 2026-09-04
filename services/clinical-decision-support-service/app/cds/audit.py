"""Audit for clinical decision support.

Two destinations, on purpose.

The tenant database gets the durable, append-only record: which actor, acting
in which clinical role, asked for a suggestion against which visit, which rules
fired, which versions produced it, and what the clinician thought of it. That
belongs with the hospital's own clinical record.

The application log gets identifiers, versions, and counts only. No patient
identifier, no complaint, no symptom, no consideration text, no credential, no
stack trace. An engineer reading the log can tell that a suggestion was issued
and which versions produced it — and nothing about who it was for.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cds import CdsDifferentialFeedback, CdsDifferentialSuggestion

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
        "chief_complaint",
        "symptoms",
        "considerations",
        "rationale",
        "explanation",
        "comment",
        "prompt",
        "api_key",
        "token",
        "database_url",
        "traceback",
    }
)

SAFE_LOG_FIELDS: tuple[str, ...] = (
    "request_id",
    "suggestion_id",
    "tenant_id",
    "actor_sub",
    "actor_role",
    "status",
    "red_flag_rule_ids",
    "prompt_version",
    "model_version",
)


async def persist_suggestion(
    db: AsyncSession,
    *,
    request_id: str,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    patient_id: UUID | None,
    response,
) -> None:
    """Append the differential suggestion record.

    Best-effort deliberately: the clinician has already been shown the result by
    the time this runs, and a database problem is an operational fault to alarm
    on rather than a reason to withhold what a clinician asked for.
    """
    row = CdsDifferentialSuggestion(
        suggestion_id=response.suggestion_id,
        request_id=request_id,
        tenant_id=tenant_id,
        actor_sub=actor_sub,
        actor_role=actor_role,
        visit_id=response.visit_id,
        patient_id=patient_id,
        department=response.department,
        status=response.status.value,
        consideration_count=len(response.considerations),
        red_flag_count=len(response.red_flags),
        red_flag_rule_ids=[flag.rule_id for flag in response.red_flags],
        knowledge_version=response.knowledge_version,
        redflag_ruleset_version=response.redflag_ruleset_version,
        prompt_version=response.prompt_version,
        model_version=response.model_version,
        evaluated_at=response.evaluated_at,
    )
    db.add(row)
    await db.commit()


async def find_suggestion(
    db: AsyncSession, suggestion_id: UUID, tenant_id: str
) -> CdsDifferentialSuggestion | None:
    """Look up a suggestion, scoped to the tenant the token resolved to."""
    row = await db.get(CdsDifferentialSuggestion, suggestion_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def persist_feedback(
    db: AsyncSession,
    *,
    request_id: str,
    suggestion_id: UUID,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    rating: str,
    comment: str | None,
) -> tuple[UUID, datetime]:
    """Append one clinician judgement and return its id and timestamp."""
    feedback_id = uuid4()
    recorded_at = datetime.now(timezone.utc)
    db.add(
        CdsDifferentialFeedback(
            feedback_id=feedback_id,
            suggestion_id=suggestion_id,
            request_id=request_id,
            tenant_id=tenant_id,
            actor_sub=actor_sub,
            actor_role=actor_role,
            rating=rating,
            comment=comment,
            created_at=recorded_at,
        )
    )
    await db.commit()
    return feedback_id, recorded_at


def log_suggestion(
    *,
    request_id: str,
    suggestion_id: UUID,
    tenant_id: str,
    actor_sub: str,
    actor_role: str,
    status: str,
    red_flag_rule_ids: list[str],
    prompt_version: str,
    model_version: str | None,
) -> None:
    """Log that a suggestion was issued. Identifiers and versions only.

    No complaint, no symptom, no consideration, and no patient identifier: this
    line goes to an ordinary application log that many people can read.
    """
    logger.info(
        "cds differential suggestion issued",
        extra={
            "request_id": request_id,
            "suggestion_id": str(suggestion_id),
            "tenant_id": tenant_id,
            "actor_sub": actor_sub,
            "actor_role": actor_role,
            "status": status,
            "red_flag_rule_ids": red_flag_rule_ids,
            "prompt_version": prompt_version,
            "model_version": model_version or "none",
        },
    )
