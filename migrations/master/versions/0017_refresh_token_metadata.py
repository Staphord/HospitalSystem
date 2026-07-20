"""Add optional IP / user-agent metadata on refresh_tokens for session dashboards.

Revision ID: 0017_refresh_token_metadata
Revises: 0016_add_subscription_request_columns
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_refresh_token_metadata"
down_revision: Union[str, Sequence[str], None] = "0016_add_subscription_request_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "refresh_tokens" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("refresh_tokens")}
    if "ip_address" not in columns:
        op.add_column(
            "refresh_tokens",
            sa.Column("ip_address", sa.String(length=45), nullable=True),
        )
    if "user_agent" not in columns:
        op.add_column(
            "refresh_tokens",
            sa.Column("user_agent", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "refresh_tokens" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("refresh_tokens")}
    if "user_agent" in columns:
        op.drop_column("refresh_tokens", "user_agent")
    if "ip_address" in columns:
        op.drop_column("refresh_tokens", "ip_address")
