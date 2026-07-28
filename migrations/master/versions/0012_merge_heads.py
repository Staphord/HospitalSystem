"""Merge master heads.

Revision ID: 0012_merge_heads
Revises: 0011_add_missing_user_columns
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_merge_heads"
down_revision: Union[str, Sequence[str], None] = ("0011_add_missing_user_columns",)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
