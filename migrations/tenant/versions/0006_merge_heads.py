"""Merge tenant migration heads.

Revision ID: 0006_merge_heads
Revises: 0005_align_patients_table_with_model, 0005_create_triage_assessments
Create Date: 2026-06-29 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_merge_heads"
down_revision: Union[tuple[str, ...], None] = (
    "0005_align_patients_table_with_model",
    "0005_create_triage_assessments",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
