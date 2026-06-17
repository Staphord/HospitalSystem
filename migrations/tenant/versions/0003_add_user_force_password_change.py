"""Add force_password_change column to tenant users table.

Revision ID: 0003_add_user_force_password_change
Revises: 0002_add_user_is_active
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_add_user_force_password_change"
down_revision: Union[str, None] = "0002_add_user_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("force_password_change", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "force_password_change")
