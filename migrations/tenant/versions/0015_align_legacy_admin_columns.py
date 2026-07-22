"""Align legacy admin tables with FR-56/FR-55 column expectations.

Legacy tenants may have audit_logs.user_sub (not user_id) and no session_id;
departments.department_id as varchar. This migration adds missing columns
without dropping legacy ones.

Revision ID: 0015_align_legacy_admin_columns
Revises: 0014_admin_module_tables
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_align_legacy_admin_columns"
down_revision: Union[str, None] = "0014_admin_module_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "audit_logs" in tables:
        cols = {c["name"] for c in inspector.get_columns("audit_logs")}
        if "user_id" not in cols and "user_sub" in cols:
            op.add_column(
                "audit_logs",
                sa.Column("user_id", sa.String(255), nullable=True),
            )
            op.execute(
                "UPDATE audit_logs SET user_id = user_sub WHERE user_id IS NULL"
            )
            op.execute(
                "UPDATE audit_logs SET user_id = 'anonymous' WHERE user_id IS NULL"
            )
            op.alter_column("audit_logs", "user_id", nullable=False)
            op.create_index("idx_audit_logs_user_id", "audit_logs", ["user_id"])
        elif "user_id" not in cols:
            op.add_column(
                "audit_logs",
                sa.Column("user_id", sa.String(255), nullable=False, server_default="anonymous"),
            )
            op.create_index("idx_audit_logs_user_id", "audit_logs", ["user_id"])

        if "session_id" not in cols:
            op.add_column(
                "audit_logs",
                sa.Column("session_id", sa.String(100), nullable=True),
            )

    if "departments" in tables:
        cols = {c["name"] for c in inspector.get_columns("departments")}
        # nothing required — ORM uses String for department_id


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "audit_logs" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("audit_logs")}
    if "session_id" in cols:
        op.drop_column("audit_logs", "session_id")
    # Keep user_id if added — safer than dropping with data
