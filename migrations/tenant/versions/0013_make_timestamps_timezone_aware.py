"""Make timestamp columns timezone aware in tenant database.

Revision ID: 0013_make_timestamps_timezone_aware
Revises: 0012_add_visit_insurance_and_queue_fields
Create Date: 2026-07-13 14:52:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_make_timestamps_timezone_aware"
down_revision: Union[str, None] = "0012_add_visit_insurance_and_queue_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _alter_ts_tz(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns(table)}
    col = cols.get(column)
    if col is None:
        return
    # Skip if already timezone-aware
    col_type = col["type"]
    if getattr(col_type, "timezone", None) is True:
        return
    op.alter_column(
        table,
        column,
        type_=sa.DateTime(timezone=True),
        postgresql_using=f"{column}::timestamptz",
    )


def upgrade() -> None:
    for table, columns in (
        ("patients", ("created_at", "updated_at")),
        ("patient_insurance", ("created_at", "verified_at")),
        ("visits", ("created_at", "updated_at")),
        ("queues", ("created_at", "called_at", "completed_at")),
    ):
        for column in columns:
            _alter_ts_tz(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _alter_ts(table: str, column: str) -> None:
        if table not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column not in cols:
            return
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            postgresql_using=f"{column}::timestamp",
        )

    for table, columns in (
        ("patients", ("created_at", "updated_at")),
        ("patient_insurance", ("created_at", "verified_at")),
        ("visits", ("created_at", "updated_at")),
        ("queues", ("created_at", "called_at", "completed_at")),
    ):
        for column in columns:
            _alter_ts(table, column)
