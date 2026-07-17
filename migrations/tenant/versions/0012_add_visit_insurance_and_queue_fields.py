"""Add missing verified_at, called_at, and completed_at columns to tenant schema.

Revision ID: 0012_add_visit_insurance_and_queue_fields
Revises: 0011_add_urgency_to_investigation_requests
Create Date: 2026-07-10 21:58:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_visit_insurance_and_queue_fields"
down_revision: Union[str, None] = "0011_add_urgency_to_investigation_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: str, col) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        op.add_column(table, col)


def upgrade() -> None:
    _add_column_if_missing(
        "patient_insurance",
        "verified_at",
        sa.Column("verified_at", sa.DateTime(), nullable=True),
    )
    _add_column_if_missing(
        "queues",
        "called_at",
        sa.Column("called_at", sa.DateTime(), nullable=True),
    )
    _add_column_if_missing(
        "queues",
        "completed_at",
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "patient_insurance" in tables:
        cols = {c["name"] for c in inspector.get_columns("patient_insurance")}
        if "verified_at" in cols:
            op.drop_column("patient_insurance", "verified_at")
    if "queues" in tables:
        cols = {c["name"] for c in inspector.get_columns("queues")}
        if "called_at" in cols:
            op.drop_column("queues", "called_at")
        if "completed_at" in cols:
            op.drop_column("queues", "completed_at")
