"""Add subscription lifecycle fields to tenants.

Revision ID: 0002_add_subscription_lifecycle
Revises: 0001_initial_master_schema
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_add_subscription_lifecycle"
down_revision: Union[str, None] = "0001_initial_master_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New tenant subscription lifecycle columns
    op.add_column("tenants", sa.Column("subscription_status", sa.String(length=32), nullable=True, index=True))
    op.add_column("tenants", sa.Column("subscription_billing_cycle", sa.String(length=16), nullable=True))
    op.add_column("tenants", sa.Column("trial_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("has_used_trial", sa.Boolean(), nullable=True))
    op.add_column("tenants", sa.Column("grace_period_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("auto_renew", sa.Boolean(), nullable=True))
    op.add_column("tenants", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("suspended_reason", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("reactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("payment_provider_id", sa.String(length=255), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("subscription_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )

    # Backfill existing tenants: treat them as active paid subscriptions unless already suspended.
    op.execute(
        """
        UPDATE tenants
        SET subscription_status = CASE
            WHEN status = 'suspended' THEN 'suspended'
            ELSE 'active'
        END,
            subscription_billing_cycle = COALESCE(subscription_billing_cycle, 'monthly'),
            has_used_trial = COALESCE(has_used_trial, false),
            auto_renew = COALESCE(auto_renew, true)
        """
    )

    # Now make the non-nullable columns strict.
    op.alter_column("tenants", "subscription_status", nullable=False)
    op.alter_column("tenants", "has_used_trial", nullable=False)
    op.alter_column("tenants", "auto_renew", nullable=False)


def downgrade() -> None:
    op.drop_column("tenants", "subscription_metadata")
    op.drop_column("tenants", "payment_provider_id")
    op.drop_column("tenants", "cancelled_at")
    op.drop_column("tenants", "reactivated_at")
    op.drop_column("tenants", "suspended_reason")
    op.drop_column("tenants", "suspended_at")
    op.drop_column("tenants", "auto_renew")
    op.drop_column("tenants", "grace_period_end")
    op.drop_column("tenants", "has_used_trial")
    op.drop_column("tenants", "trial_end")
    op.drop_column("tenants", "trial_start")
    op.drop_column("tenants", "subscription_billing_cycle")
    op.drop_column("tenants", "subscription_status")
