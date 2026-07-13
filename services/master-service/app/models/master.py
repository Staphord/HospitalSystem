import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    Boolean,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSON

from app.db.base import Base


class TenantStatus(str, PyEnum):
    trial = "trial"
    active = "active"
    suspended = "suspended"
    terminated = "terminated"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), unique=True, index=True, nullable=False)

    # Official hospital identification
    hospital_name = Column(String(200), nullable=False)
    country = Column(String(100), nullable=False, default="")
    city = Column(String(100), nullable=False, default="")
    address = Column(Text, nullable=True)

    # Primary contact
    primary_contact_name = Column(String(200), nullable=False, default="")
    primary_contact_email = Column(String(150), nullable=False, default="")
    primary_contact_phone = Column(String(20), nullable=False, default="")

    # Billing & localization
    billing_email = Column(String(150), nullable=False, default="")
    timezone = Column(String(50), nullable=False, default="UTC")
    currency = Column(String(5), nullable=False, default="USD")
    date_format = Column(String(20), nullable=False, default="%Y-%m-%d")

    # Branding / infra
    logo_url = Column(String(255), nullable=True)
    data_region = Column(String(50), nullable=True)
    db_connection_string = Column(Text, nullable=False)  # encrypted connection string

    # Lifecycle
    status = Column(String(32), default=TenantStatus.trial.value, nullable=False)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # Audit
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admins.super_admin_id"),
        nullable=False,
    )
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

    # Legacy subscription denormalization kept for quick reads.
    # Authoritative subscription data lives in the subscriptions table.
    subscription_plan = Column(String(64), default="standard")
    subscription_status = Column(String(32), default="active", nullable=False, index=True)
    subscription_billing_cycle = Column(String(16), default="monthly")
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True)
    trial_start = Column(DateTime(timezone=True), nullable=True)
    trial_end = Column(DateTime(timezone=True), nullable=True)
    has_used_trial = Column(Boolean, default=False, nullable=False)
    grace_period_days = Column(Integer, default=7, nullable=False)
    grace_period_end = Column(DateTime(timezone=True), nullable=True)
    auto_renew = Column(Boolean, default=True, nullable=False)
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    suspended_reason = Column(Text, nullable=True)
    reactivated_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    terminated_at = Column(DateTime(timezone=True), nullable=True)
    termination_reason = Column(Text, nullable=True)
    payment_provider_id = Column(String(255), nullable=True)
    subscription_metadata = Column("subscription_metadata", JSON, nullable=True)

    # Deferred downgrade: pending plan applied at next renewal
    pending_plan = Column(String(64), nullable=True)
    pending_billing_cycle = Column(String(16), nullable=True)

    keycloak_realm = Column(String(255), nullable=True, default="hospital-realm")
    is_active = Column(Boolean, default=True, nullable=False)


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
