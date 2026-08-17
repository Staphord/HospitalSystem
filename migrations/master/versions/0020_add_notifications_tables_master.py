"""Add notifications and notification_preferences tables to master DB.

Revision ID: 0020_add_notifications_tables_master
Revises: 0019_add_grace_period_days
Create Date: 2026-08-13 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0020_add_notifications_tables_master"
down_revision: Union[str, None] = "0019_add_grace_period_days"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "notifications" not in tables:
        op.create_table(
            "notifications",
            sa.Column("notification_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", sa.String(50), nullable=False, index=True),
            sa.Column("recipient_id", sa.String(100), nullable=True, index=True),
            sa.Column("recipient_role", sa.String(50), nullable=True, index=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("category", sa.String(50), nullable=False, server_default="system", index=True),
            sa.Column("priority", sa.String(30), nullable=False, server_default="normal"),
            sa.Column("status", sa.String(30), nullable=False, server_default="unread", index=True),
            sa.Column("action_url", sa.String(500), nullable=True),
            sa.Column("metadata_payload", postgresql.JSONB(), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
                index=True,
            ),
        )
        op.create_index("idx_notifications_recipient_status", "notifications", ["recipient_id", "status"])
        op.create_index("idx_notifications_role_status", "notifications", ["recipient_role", "status"])

    if "notification_preferences" not in tables:
        op.create_table(
            "notification_preferences",
            sa.Column("preference_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", sa.String(100), nullable=False, index=True),
            sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("sms_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("categories_disabled", postgresql.JSONB(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "notification_preferences" in tables:
        op.drop_table("notification_preferences")
    if "notifications" in tables:
        op.drop_table("notifications")
