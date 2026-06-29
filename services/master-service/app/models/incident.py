import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentSeverity(str):
    warning = "warning"
    severe = "severe"


class IncidentStatus(str):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    closed = "closed"


class Incident(Base):
    """System incidents reported by super-admins or automated monitors."""

    __tablename__ = "incidents"

    incident_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(16), nullable=False, default="warning", index=True)
    status = Column(String(32), nullable=False, default="open", index=True)
    source = Column(String(100), nullable=True)
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=True,
        index=True,
    )
    assigned_to = Column(
        String(64),
        ForeignKey("super_admins.super_admin_id"),
        nullable=True,
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    created_by = Column(
        String(64),
        ForeignKey("super_admins.super_admin_id"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)
