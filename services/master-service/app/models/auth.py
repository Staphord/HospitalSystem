"""Authentication session models.

Stores active refresh-token / session records for super-admin users so that
the /sessions endpoints can list and revoke live sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RefreshToken(Base):
    """Tracks issued refresh tokens (sessions) for super-admin users."""

    __tablename__ = "refresh_tokens"

    # Unique session identifier (used by revoke endpoint)
    session_id = Column(String(128), primary_key=True, default=lambda: str(uuid.uuid4()))

    # The Keycloak subject UUID that owns this session
    keycloak_sub = Column(String(255), nullable=False, index=True)

    # Hashed refresh token value (or "impersonation:<tenant_id>" for impersonation sessions)
    refresh_token_hash = Column(String(512), nullable=False)

    # Session lifecycle
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    # Optional request metadata for the sessions dashboard
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
