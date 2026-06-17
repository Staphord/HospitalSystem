"""SaaS billing, subscription, audit and announcement models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SubscriptionPlan(Base):
    """Canonical subscription tiers and limits."""

    __tablename__ = "subscription_plans"

    plan_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    plan_name = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    max_users = Column(Integer, nullable=True)  # null = unlimited
    max_patients = Column(Integer, nullable=True)  # null = unlimited
    storage_gb = Column(Integer, nullable=False, default=0)
    modules_included = Column(JSONB, nullable=False, default=list)

    monthly_price = Column(Numeric(10, 2), nullable=False, default=0)
    annual_price = Column(Numeric(10, 2), nullable=False, default=0)
    annual_discount_pct = Column(Numeric(4, 1), nullable=False, default=0)
    uptime_sla_pct = Column(Numeric(5, 2), nullable=False, default=99.9)
    backup_frequency_hours = Column(Integer, nullable=False, default=24)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SubscriptionStatus(str):
    trial = "trial"
    active = "active"
    grace = "grace"
    suspended = "suspended"
    cancelled = "cancelled"
    terminated = "terminated"


class Subscription(Base):
    """Per-tenant subscription record."""

    __tablename__ = "subscriptions"

    subscription_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.plan_id"),
        nullable=False,
    )

    billing_cycle = Column(String(16), nullable=False, default="monthly")
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    grace_period_days = Column(Integer, nullable=False, default=7)
    auto_renew = Column(Boolean, nullable=False, default=True)
    status = Column(String(32), nullable=False, default="trial", index=True)

    suspended_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class InvoiceStatus(str):
    unpaid = "unpaid"
    paid = "paid"
    overdue = "overdue"
    void = "void"


class Invoice(Base):
    """Subscription invoices generated per billing cycle."""

    __tablename__ = "invoices"

    invoice_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=False,
        index=True,
    )
    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.subscription_id"),
        nullable=False,
        index=True,
    )

    invoice_number = Column(String(30), nullable=False, unique=True, index=True)
    billing_period_start = Column(Date, nullable=False)
    billing_period_end = Column(Date, nullable=False)
    plan_name = Column(String(50), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(5), nullable=False, default="USD")
    due_date = Column(Date, nullable=False)
    status = Column(String(32), nullable=False, default="unpaid")

    issued_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)


class SaaSPayment(Base):
    """Payment receipts from hospitals for subscriptions."""

    __tablename__ = "saas_payments"

    payment_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.invoice_id"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=False,
        index=True,
    )

    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(5), nullable=False, default="USD")
    payment_method = Column(String(50), nullable=False)
    reference_number = Column(String(100), nullable=True)

    recorded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admins.super_admin_id"),
        nullable=False,
    )
    receipt_sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SuperAdminAuditLog(Base):
    """Tamper-proof log of super-admin actions across tenants."""

    __tablename__ = "super_admin_audit_log"

    log_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    super_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admins.super_admin_id"),
        nullable=False,
        index=True,
    )
    action = Column(String(100), nullable=False, index=True)
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=True,
        index=True,
    )
    action_detail = Column(JSONB, nullable=True)
    is_impersonation = Column(Boolean, nullable=False, default=False)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AnnouncementAudience(str):
    all_users = "all"
    selected = "selected"


class Announcement(Base):
    """System maintenance notices broadcast to tenants."""

    __tablename__ = "announcements"

    announcement_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    audience = Column(String(16), nullable=False, default="all")
    target_tenant_ids = Column(JSONB, nullable=True)
    publish_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admins.super_admin_id"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SubscriptionEventType(str):
    plan_created = "plan_created"
    plan_upgraded = "plan_upgraded"
    plan_downgraded = "plan_downgraded"
    payment_received = "payment_received"
    suspension = "suspension"
    reactivation = "reactivation"
    termination = "termination"
    grace_period_started = "grace_period_started"
    cancellation_requested = "cancellation_requested"


class SubscriptionAuditLog(Base):
    """Log of all subscription lifecycle events."""

    __tablename__ = "subscription_audit_log"

    event_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id"),
        nullable=False,
        index=True,
    )
    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.subscription_id"),
        nullable=True,
        index=True,
    )

    event_type = Column(String(64), nullable=False, index=True)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_type = Column(String(32), nullable=False, default="system")
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
