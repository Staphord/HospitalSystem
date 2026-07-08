"""Merge radiology and consultation migrations

Revision ID: 0009_merge_radiology_and_consultation
Revises: 0007_add_pharmacy_inventory, 0008_alter_visits_patient_id_to_uuid
Create Date: 2026-06-29 14:03:52.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009_merge_radiology_and_consultation'
down_revision: Union[str, None] = ('0007_add_pharmacy_inventory', '0008_alter_visits_patient_id_to_uuid')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
