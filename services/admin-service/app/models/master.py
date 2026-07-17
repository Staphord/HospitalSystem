import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Integer, String, Text, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), unique=True, index=True, nullable=False)
    hospital_name = Column(String(200), nullable=False)
    country = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    primary_contact_name = Column(String(200), nullable=True)
    primary_contact_email = Column(String(150), nullable=True)
    primary_contact_phone = Column(String(20), nullable=True)
    billing_email = Column(String(150), nullable=True)
    timezone = Column(String(50), nullable=True, default="UTC")
    currency = Column(String(5), nullable=True, default="USD")
    date_format = Column(String(20), nullable=True)
    logo_url = Column(Text, nullable=True)
    db_connection_string = Column(Text, nullable=False)
    status = Column(String(32), default="active", nullable=False)
    subscription_plan = Column(String(64), default="standard")
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    keycloak_realm = Column(String(255), nullable=True, default="hospital-realm")
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


class TenantRole(Base):
    """Roles created by hospital admin within a specific tenant (stored in Master DB)."""

    __tablename__ = "tenant_roles"

    tenant_role_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=lambda: uuid.uuid4(),
    )
    tenant_id = Column(
        String(64),
        nullable=False,
        index=True,
    )
    name = Column(String(50), nullable=False, index=True)
    description = Column(String(500), nullable=True)
    scope = Column(JSONB, nullable=True)

    created_by = Column(String(255), nullable=True)
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

    __table_args__ = (
        UniqueConstraint("name", "tenant_id", name="uq_tenant_role_name_per_tenant"),
    )
