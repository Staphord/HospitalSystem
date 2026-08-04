"""Merge all tenant heads

Revision ID: 0020_merge_tenant_heads
Revises: 0016_merge_tenant_heads, 0016_add_specimen_label_to_laboratory, 0019_create_notifications_tables
Create Date: 2026-07-31 10:30:00.000000+00:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0020_merge_tenant_heads'
down_revision: Union[tuple[str, ...], None] = (
    '0016_merge_tenant_heads',
    '0016_add_specimen_label_to_laboratory',
    '0019_create_notifications_tables'
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
