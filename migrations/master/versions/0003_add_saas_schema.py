"""Add SaaS subscription, billing, audit and announcement tables.

Revision ID: 0003_add_saas_schema
Revises: 0002_add_subscription_lifecycle
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_add_saas_schema"
down_revision: Union[str, None] = "0002_add_subscription_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extend tenants table with full SaaS registration fields
    # ------------------------------------------------------------------
    op.add_column("tenants", sa.Column("country", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("city", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("primary_contact_name", sa.String(length=200), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("primary_contact_email", sa.String(length=150), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("primary_contact_phone", sa.String(length=20), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("billing_email", sa.String(length=150), nullable=False, server_default=""))
    op.add_column("tenants", sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"))
    op.add_column("tenants", sa.Column("currency", sa.String(length=5), nullable=False, server_default="USD"))
    op.add_column("tenants", sa.Column("date_format", sa.String(length=20), nullable=False, server_default="%Y-%m-%d"))
    op.add_column("tenants", sa.Column("logo_url", sa.String(length=255), nullable=True))
    op.add_column("tenants", sa.Column("data_region", sa.String(length=50), nullable=True))
    op.add_column("tenants", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("super_admins.super_admin_id"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Subscription plans catalog
    # ------------------------------------------------------------------
    op.create_table(
        "subscription_plans",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_name", sa.String(length=50), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("max_patients", sa.Integer(), nullable=True),
        sa.Column("storage_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("modules_included", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("monthly_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("annual_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("annual_discount_pct", sa.Numeric(4, 1), nullable=False, server_default="0"),
        sa.Column("uptime_sla_pct", sa.Numeric(5, 2), nullable=False, server_default="99.9"),
        sa.Column("backup_frequency_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------
    # Per-tenant subscriptions
    # ------------------------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.tenant_id"), nullable=False, index=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subscription_plans.plan_id"), nullable=False),
        sa.Column("billing_cycle", sa.String(length=16), nullable=False, server_default="monthly"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("grace_period_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="trial", index=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------
    op.create_table(
        "invoices",
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.tenant_id"), nullable=False, index=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subscriptions.subscription_id"), nullable=False, index=True),
        sa.Column("invoice_number", sa.String(length=30), nullable=False, unique=True),
        sa.Column("billing_period_start", sa.Date(), nullable=False),
        sa.Column("billing_period_end", sa.Date(), nullable=False),
        sa.Column("plan_name", sa.String(length=50), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=5), nullable=False, server_default="USD"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unpaid"),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # SaaS payments
    # ------------------------------------------------------------------
    op.create_table(
        "saas_payments",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.invoice_id"), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.tenant_id"), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=5), nullable=False, server_default="USD"),
        sa.Column("payment_method", sa.String(length=50), nullable=False),
        sa.Column("reference_number", sa.String(length=100), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("super_admins.super_admin_id"), nullable=False),
        sa.Column("receipt_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------
    # Super admin audit log
    # ------------------------------------------------------------------
    op.create_table(
        "super_admin_audit_log",
        sa.Column("log_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("super_admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("super_admins.super_admin_id"), nullable=False, index=True),
        sa.Column("action", sa.String(length=100), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.tenant_id"), nullable=True, index=True),
        sa.Column("action_detail", postgresql.JSONB(), nullable=True),
        sa.Column("is_impersonation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------
    op.create_table(
        "announcements",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("target_tenant_ids", postgresql.JSONB(), nullable=True),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("super_admins.super_admin_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------
    # Subscription audit log
    # ------------------------------------------------------------------
    op.create_table(
        "subscription_audit_log",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.tenant_id"), nullable=False, index=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subscriptions.subscription_id"), nullable=True, index=True),
        sa.Column("event_type", sa.String(length=64), nullable=False, index=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("subscription_audit_log")
    op.drop_table("announcements")
    op.drop_table("super_admin_audit_log")
    op.drop_table("saas_payments")
    op.drop_table("invoices")
    op.drop_table("subscriptions")
    op.drop_table("subscription_plans")

    op.drop_column("tenants", "created_by")
    op.drop_column("tenants", "trial_ends_at")
    op.drop_column("tenants", "data_region")
    op.drop_column("tenants", "logo_url")
    op.drop_column("tenants", "date_format")
    op.drop_column("tenants", "currency")
    op.drop_column("tenants", "timezone")
    op.drop_column("tenants", "billing_email")
    op.drop_column("tenants", "primary_contact_phone")
    op.drop_column("tenants", "primary_contact_email")
    op.drop_column("tenants", "primary_contact_name")
    op.drop_column("tenants", "address")
    op.drop_column("tenants", "city")
    op.drop_column("tenants", "country")
