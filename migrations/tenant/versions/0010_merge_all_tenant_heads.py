"""Merge all tenant head revisions

Revision ID: 0010_merge_all_tenant_heads
Revises: 0007_add_pharmacy_inventory, 0009_merge_radiology_and_consultation
Create Date: 2026-07-06 18:10:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010_merge_all_tenant_heads'
down_revision: Union[tuple[str, ...], None] = (
    '0007_add_pharmacy_inventory',
    '0009_merge_radiology_and_consultation'
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
