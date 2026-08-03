"""Add admission condition, nursing note resp rate, and visitor phone.

Revision ID: 0019_admission_condition_and_vitals
Revises: 0018_ward_visitors_handover
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019_admission_condition_and_vitals"
down_revision: Union[str, None] = "0018_ward_visitors_handover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "admissions" in tables:
        cols = {c["name"] for c in inspector.get_columns("admissions")}
        if "condition" not in cols:
            op.add_column(
                "admissions",
                sa.Column("condition", sa.String(32), nullable=False, server_default="stable"),
            )

    if "nursing_notes" in tables:
        cols = {c["name"] for c in inspector.get_columns("nursing_notes")}
        if "vitals_resp_rate" not in cols:
            op.add_column(
                "nursing_notes",
                sa.Column("vitals_resp_rate", sa.Integer(), nullable=True),
            )

    if "visitor_logs" in tables:
        cols = {c["name"] for c in inspector.get_columns("visitor_logs")}
        if "visitor_phone" not in cols:
            op.add_column(
                "visitor_logs",
                sa.Column("visitor_phone", sa.String(30), nullable=True),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "visitor_logs" in tables:
        cols = {c["name"] for c in inspector.get_columns("visitor_logs")}
        if "visitor_phone" in cols:
            op.drop_column("visitor_logs", "visitor_phone")

    if "nursing_notes" in tables:
        cols = {c["name"] for c in inspector.get_columns("nursing_notes")}
        if "vitals_resp_rate" in cols:
            op.drop_column("nursing_notes", "vitals_resp_rate")

    if "admissions" in tables:
        cols = {c["name"] for c in inspector.get_columns("admissions")}
        if "condition" in cols:
            op.drop_column("admissions", "condition")
