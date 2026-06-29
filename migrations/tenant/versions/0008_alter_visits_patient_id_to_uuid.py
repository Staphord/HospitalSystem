"""Alter visits patient_id to UUID.

Revision ID: 0008_alter_visits_patient_id_to_uuid
Revises: 0007_create_consultations
Create Date: 2026-06-29 16:56:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_alter_visits_patient_id_to_uuid"
down_revision: Union[str, None] = "0007_create_consultations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alter patient_id in visits table to UUID by casting the varchar column
    op.execute("ALTER TABLE visits ALTER COLUMN patient_id TYPE uuid USING patient_id::uuid")


def downgrade() -> None:
    op.execute("ALTER TABLE visits ALTER COLUMN patient_id TYPE varchar(36)")
