"""Add is_active column to tenant users table.

Revision ID: 0002_add_user_is_active
Revises: 0001_initial_tenant_schema
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_add_user_is_active"
down_revision: Union[str, None] = "0001_initial_tenant_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "is_active")
