"""Tenant-database tables owned by the clinical decision support service.

Both tables are append-only. Nothing in this service updates or deletes a row,
and migration 0027 installs a trigger that refuses UPDATE and DELETE at the
database level as well, so a record of what a clinician was shown and what they
did about it cannot be quietly rewritten later.

What is deliberately not stored: patient names, chief complaints, symptoms,
considerations, and rationale. A suggestion is recorded by its id together with
the rule ids that fired and the versions that produced it, which is enough for
an auditor to trace it without keeping a second copy of clinical narrative in an
audit table.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID

# JSONB in PostgreSQL, plain JSON everywhere else, so the in-memory SQLite the
# test suite uses builds the same schema.
JsonList = JSON().with_variant(JSONB(), "postgresql")

from app.db.base import Base


class CdsDifferentialSuggestion(Base):
    """One differential suggestion that was issued to a clinician.

    Records what was produced and by which versions, never what it said. The
    considerations, rationale, and chief complaint are deliberately absent: an
    auditor needs to know a suggestion happened, who saw it, and which knowledge,
    rule pack, prompt, and model produced it, not a second copy of the clinical
    narrative.
    """

    __tablename__ = "cds_differential_suggestions"

    suggestion_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)
    actor_role = Column(String(64), nullable=False)
    visit_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    department = Column(String(60), nullable=False)
    status = Column(String(40), nullable=False)
    consideration_count = Column(Integer, nullable=False, default=0)
    red_flag_count = Column(Integer, nullable=False, default=0)
    # Which deterministic rules fired, by id. Identifiers only, no rule text.
    red_flag_rule_ids = Column(JsonList, nullable=False, default=list)

    knowledge_version = Column(String(64), nullable=False)
    redflag_ruleset_version = Column(String(64), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    model_version = Column(String(120), nullable=True)

    evaluated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CdsDifferentialFeedback(Base):
    """One clinician's judgement of one suggestion.

    Recorded so a human can review whether the workflow is helping. Nothing in
    this service reads it back: feedback that silently altered future output
    would be an unreviewed learning loop, which the phase rules forbid.
    """

    __tablename__ = "cds_differential_feedback"

    feedback_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suggestion_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cds_differential_suggestions.suggestion_id"),
        nullable=False,
        index=True,
    )
    request_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)
    actor_role = Column(String(64), nullable=False)

    rating = Column(String(20), nullable=False)
    # Entered by the clinician, so it stays in the tenant database with the rest
    # of that hospital's record and never reaches a shared log.
    comment = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
