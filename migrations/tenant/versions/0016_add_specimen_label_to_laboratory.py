"""Add specimen_label column to specimens and lab_results tables.

Revision ID: 0016_add_specimen_label_to_laboratory
Revises: 0015_update_consultation_schema
Create Date: 2026-07-20 13:35:00.000000+03:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0016_add_specimen_label_to_laboratory"
down_revision: Union[str, None] = "0015_update_consultation_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("specimens", sa.Column("specimen_label", sa.String(length=50), nullable=True))
    op.add_column("lab_results", sa.Column("specimen_label", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("lab_results", "specimen_label")
    op.drop_column("specimens", "specimen_label")
