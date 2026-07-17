"""Add subscription request/approval columns to tenants table.

Revision ID: 0016_add_subscription_request_columns
Revises: 0015_add_billing_columns
Create Date: 2026-07-14 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_add_subscription_request_columns"
down_revision: Union[str, Sequence[str], None] = "0015_add_billing_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("pending_action", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("requested_plan", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("request_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "review_notes")
    op.drop_column("tenants", "reviewed_at")
    op.drop_column("tenants", "reviewed_by")
    op.drop_column("tenants", "requested_at")
    op.drop_column("tenants", "request_reason")
    op.drop_column("tenants", "requested_plan")
    op.drop_column("tenants", "pending_action")
