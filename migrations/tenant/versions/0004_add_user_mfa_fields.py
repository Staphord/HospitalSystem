"""Add MFA fields to users table.

Revision ID: 0004_add_user_mfa_fields
Revises: 0003_add_user_force_password_change
Create Date: 2026-06-19 00:00:00.000000+00:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_add_user_mfa_fields"
down_revision: Union[str, None] = "0003_add_user_force_password_change"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("backup_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_secret")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "backup_codes")
