"""Add missing grace_period_days column to tenants table.

Revision ID: 0019_add_grace_period_days
Revises: 0018_merge_master_heads
Create Date: 2026-08-13 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_add_grace_period_days"
down_revision: Union[str, None] = "0018_merge_master_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("grace_period_days", sa.Integer(), nullable=False, server_default="7"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "grace_period_days")
