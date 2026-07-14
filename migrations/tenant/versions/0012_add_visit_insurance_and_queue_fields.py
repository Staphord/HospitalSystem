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


def upgrade() -> None:
    # 1. Add verified_at to patient_insurance
    op.add_column(
        "patient_insurance",
        sa.Column("verified_at", sa.DateTime(), nullable=True)
    )
    # 2. Add called_at and completed_at to queues
    op.add_column(
        "queues",
        sa.Column("called_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "queues",
        sa.Column("completed_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("patient_insurance", "verified_at")
    op.drop_column("queues", "called_at")
    op.drop_column("queues", "completed_at")
