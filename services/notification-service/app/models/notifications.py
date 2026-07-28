import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text, JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class Notification(Base):
    """Notification ORM model for storing system and clinical alerts."""

    __tablename__ = "notifications"

    notification_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), nullable=False, index=True)
    recipient_id = Column(String(100), nullable=True, index=True)
    recipient_role = Column(String(50), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    category = Column(String(50), nullable=False, default="system", index=True)
    priority = Column(String(30), nullable=False, default="normal")
    status = Column(String(30), nullable=False, default="unread", index=True)
    action_url = Column(String(500), nullable=True)
    metadata_payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )


class NotificationPreference(Base):
    """User preferences model for notification channel delivery."""

    __tablename__ = "notification_preferences"

    preference_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100), nullable=False, index=True)
    in_app_enabled = Column(Boolean, nullable=False, default=True)
    email_enabled = Column(Boolean, nullable=False, default=True)
    sms_enabled = Column(Boolean, nullable=False, default=False)
    categories_disabled = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
