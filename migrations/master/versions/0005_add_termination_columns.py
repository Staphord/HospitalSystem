"""Add termination columns to tenants.

Revision ID: 0005_add_termination_columns
Revises: 0004_announcement_creator_null
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_add_termination_columns"
down_revision: Union[str, None] = "0004_announcement_creator_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("termination_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "termination_reason")
    op.drop_column("tenants", "terminated_at")
