"""Add keycloak_realm column to refresh_tokens table.

Revision ID: 0021_add_keycloak_realm_to_refresh_tokens
Revises: 0020_add_notifications_tables_master
Create Date: 2026-08-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_add_keycloak_realm_to_refresh_tokens"
down_revision: Union[str, None] = "0020_add_notifications_tables_master"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "refresh_tokens" in tables:
        columns = {c["name"] for c in inspector.get_columns("refresh_tokens")}
        if "keycloak_realm" not in columns:
            op.add_column(
                "refresh_tokens",
                sa.Column("keycloak_realm", sa.String(length=255), nullable=True),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "refresh_tokens" in tables:
        columns = {c["name"] for c in inspector.get_columns("refresh_tokens")}
        if "keycloak_realm" in columns:
            op.drop_column("refresh_tokens", "keycloak_realm")
