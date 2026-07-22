"""Add billing_cleared to visits table.

Revision ID: 0015_add_visit_billing_cleared
Revises: 0014_add_prescriptions_and_dispensing
Create Date: 2026-07-17 17:00:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_add_visit_billing_cleared"
down_revision: Union[str, None] = "0014_add_prescriptions_and_dispensing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column("billing_cleared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("visits", "billing_cleared")
