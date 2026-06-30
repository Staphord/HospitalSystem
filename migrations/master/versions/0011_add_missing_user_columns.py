"""Add missing columns to users table.

Revision ID: 0011_add_missing_user_columns
Revises: 0010_add_global_and_tenant_roles
Create Date: 2026-06-26 23:59:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011_add_missing_user_columns"
down_revision: Union[str, None] = "0010_add_global_and_tenant_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("force_password_change", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("backup_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "backup_codes")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
    op.drop_column("users", "force_password_change")
    op.drop_column("users", "is_active")
