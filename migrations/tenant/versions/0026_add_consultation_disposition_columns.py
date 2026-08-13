"""Add consultation disposition/follow-up columns.

services/consultation-service/app/models/consultation.py's Consultation model
has defined admission_reason, discharge_instructions, follow_up_date,
return_date, and return_reason since that service was built, but no
migration ever added them — a service-level `_migrate_consultation_columns()`
helper patched them onto the live table at every tenant DB connection instead
(removed in this same change, see services/consultation-service/app/db/tenant.py).
That patch also touched bill_items, which migration 0024 already reconciles
properly; only the consultations columns needed a real migration here.

Revision ID: 0026_add_consultation_disposition_columns
Revises: 0025_add_payments_table
Create Date: 2026-08-13 22:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_add_consultation_disposition_columns"
down_revision: Union[str, None] = "0025_add_payments_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "consultations" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("consultations")}
    if "admission_reason" not in columns:
        op.add_column("consultations", sa.Column("admission_reason", sa.Text(), nullable=True))
    if "discharge_instructions" not in columns:
        op.add_column("consultations", sa.Column("discharge_instructions", sa.Text(), nullable=True))
    if "follow_up_date" not in columns:
        op.add_column("consultations", sa.Column("follow_up_date", sa.Date(), nullable=True))
    if "return_date" not in columns:
        op.add_column("consultations", sa.Column("return_date", sa.Date(), nullable=True))
    if "return_reason" not in columns:
        op.add_column("consultations", sa.Column("return_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "consultations" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("consultations")}
    for col in ("return_reason", "return_date", "follow_up_date", "discharge_instructions", "admission_reason"):
        if col in columns:
            op.drop_column("consultations", col)
