"""Merge all tenant heads

Revision ID: 0016_merge_tenant_heads
Revises: 0010_merge_all_tenant_heads, 0015_add_visit_billing_cleared
Create Date: 2026-07-20 17:00:00.000000+00:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0016_merge_tenant_heads'
down_revision: Union[tuple[str, ...], None] = (
    '0010_merge_all_tenant_heads',
    '0015_add_visit_billing_cleared'
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
