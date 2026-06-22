"""Add MFA fields to super admins.

Revision ID: 0007_add_superadmin_mfa_fields
Revises: 0006_add_keycloak_realm
Create Date: 2026-06-19 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_add_superadmin_mfa_fields"
down_revision: Union[str, None] = "0006_add_keycloak_realm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "super_admins",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("super_admins", sa.Column("backup_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("super_admins", "backup_codes")
    op.drop_column("super_admins", "mfa_enabled")
