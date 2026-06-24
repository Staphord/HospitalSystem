"""Add session metadata to refresh tokens.

Revision ID: 0008_add_session_meta
Revises: 0007_add_incidents, 0007_add_superadmin_mfa_fields
Create Date: 2026-06-24 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_add_session_meta"
down_revision: Union[str, Sequence[str], None] = (
    "0007_add_incidents",
    "0007_add_superadmin_mfa_fields",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("ip_address", sa.String(45), nullable=True))
    op.add_column("refresh_tokens", sa.Column("user_agent", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "user_agent")
    op.drop_column("refresh_tokens", "ip_address")
