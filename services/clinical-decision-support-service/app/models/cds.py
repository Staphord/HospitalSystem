"""Tenant-database tables owned by the clinical decision support service.

Both tables are append-only. Nothing in this service updates or deletes a row,
and migration 0027 installs a trigger that refuses UPDATE and DELETE at the
database level as well, so a record of what a clinician was shown and what they
did about it cannot be quietly rewritten later.

What is deliberately not stored: patient names, drug names, finding text, and
explanations. A finding is recorded by its content-derived finding_id, and the
ruleset version that produced it is recorded alongside, which is enough to
reproduce exactly what was shown without keeping a second copy of clinical
narrative in an audit table.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID

# JSONB in PostgreSQL, plain JSON everywhere else, so the in-memory SQLite the
# test suite uses builds the same schema.
FindingIndex = JSON().with_variant(JSONB(), "postgresql")

from app.db.base import Base


class CdsMedicationCheck(Base):
    __tablename__ = "cds_medication_checks"

    check_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)
    actor_role = Column(String(64), nullable=False)
    visit_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    status = Column(String(40), nullable=False)
    finding_count = Column(Integer, nullable=False, default=0)
    alert_count = Column(Integer, nullable=False, default=0)
    needs_review_count = Column(Integer, nullable=False, default=0)

    # Which findings this check produced, as {finding_id: {status, blocking}}.
    # Identifiers and states only, no clinical text. It exists so that an
    # acknowledgement can be verified against the check it claims to belong to:
    # without it, any finding id at all could be acknowledged.
    finding_index = Column(FindingIndex, nullable=False, default=dict)

    ruleset_source = Column(String(120), nullable=True)
    ruleset_version = Column(String(64), nullable=True)
    ruleset_effective_date = Column(Date, nullable=True)
    ruleset_stale = Column(Boolean, nullable=False, default=False)

    evaluated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CdsAlertAction(Base):
    __tablename__ = "cds_alert_actions"

    action_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cds_medication_checks.check_id"),
        nullable=False,
        index=True,
    )
    finding_id = Column(String(64), nullable=False, index=True)
    action = Column(String(20), nullable=False)

    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)
    actor_role = Column(String(64), nullable=False)
    request_id = Column(String(64), nullable=False, index=True)

    # Required for an override, absent for an acknowledgement. Entered by the
    # clinician, so it stays in the tenant database with the rest of that
    # hospital's clinical record and never reaches a shared log.
    reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
