"""Merge master migration heads.

Revision ID: 0018_merge_master_heads
Revises: 0012_merge_heads, 0017_refresh_token_metadata
"""

from typing import Sequence, Union


revision: str = "0018_merge_master_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0012_merge_heads",
    "0017_refresh_token_metadata",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
