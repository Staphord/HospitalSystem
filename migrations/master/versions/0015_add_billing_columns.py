"""Add billing columns.

Revision ID: 0015_add_billing_columns
Revises: 0014_widen_logo_url_to_text
Create Date: 2026-07-13 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers
revision: str = "0015_add_billing_columns"
down_revision: Union[str, Sequence[str], None] = "0014_widen_logo_url_to_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add reminder_sent_at to invoices
    op.add_column(
        "invoices",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Add receipt_delivery_status to saas_payments
    op.add_column(
        "saas_payments",
        sa.Column(
            "receipt_delivery_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    op.drop_column("saas_payments", "receipt_delivery_status")
    op.drop_column("invoices", "reminder_sent_at")
