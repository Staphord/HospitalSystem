from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Integer, String, Text, Boolean

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), unique=True, index=True, nullable=False)
    hospital_name = Column(String(200), nullable=False)
    db_connection_string = Column(Text, nullable=False)
    status = Column(String(32), default="active", nullable=False)
    subscription_plan = Column(String(64), default="standard")
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class GlobalAuditLog(Base):
    __tablename__ = "global_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=True)
    user_sub = Column(String(255), index=True, nullable=True)
    action = Column(String(64), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AssistantConfig(Base):
    """Platform-wide Hospital Assistant configuration, owned by the super admin.

    One row, id 1. It lives in the master database rather than in any tenant
    database because it configures the platform's own model provider, not a
    hospital's data: one key, one model, one set of bounds for every tenant.
    No tenant administrator can read or write it.

    The API key is stored encrypted with the same Fernet key that protects
    tenant connection strings, and is never returned to a browser - the read
    endpoint sends a masked hint and a boolean instead.
    """

    __tablename__ = "assistant_config"

    id = Column(Integer, primary_key=True)

    # Provider
    provider = Column(String(32), nullable=False, server_default="groq")
    api_key_encrypted = Column(Text, nullable=True)
    base_url = Column(
        String(255), nullable=False, server_default="https://api.groq.com/openai/v1"
    )
    model = Column(String(128), nullable=False, server_default="openai/gpt-oss-120b")
    transcription_model = Column(
        String(128), nullable=False, server_default="whisper-large-v3"
    )

    # Request bounds
    max_question_chars = Column(Integer, nullable=False, server_default="2000")
    request_timeout_seconds = Column(Integer, nullable=False, server_default="20")

    # Chat history bounds, per staff member
    history_max_conversations = Column(Integer, nullable=False, server_default="50")
    history_max_messages = Column(Integer, nullable=False, server_default="200")

    # Push-to-talk voice bounds
    max_audio_bytes = Column(Integer, nullable=False, server_default="5242880")
    max_audio_duration_ms = Column(Integer, nullable=False, server_default="60000")
    voice_timeout_seconds = Column(Integer, nullable=False, server_default="20")

    # Live operational figures
    live_data_cache_seconds = Column(Integer, nullable=False, server_default="30")
    live_data_timeout_seconds = Column(Integer, nullable=False, server_default="3")
    live_data_max_metrics = Column(Integer, nullable=False, server_default="3")

    updated_by = Column(String(255), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
