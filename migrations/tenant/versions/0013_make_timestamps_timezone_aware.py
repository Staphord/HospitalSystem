"""Make timestamp columns timezone aware in tenant database.

Revision ID: 0013_make_timestamps_timezone_aware
Revises: 0012_add_visit_insurance_and_queue_fields
Create Date: 2026-07-13 14:52:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_make_timestamps_timezone_aware"
down_revision: Union[str, None] = "0012_add_visit_insurance_and_queue_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update patients
    op.alter_column('patients', 'created_at', type_=sa.DateTime(timezone=True), postgresql_using='created_at::timestamptz')
    op.alter_column('patients', 'updated_at', type_=sa.DateTime(timezone=True), postgresql_using='updated_at::timestamptz')

    # 2. Update patient_insurance
    op.alter_column('patient_insurance', 'created_at', type_=sa.DateTime(timezone=True), postgresql_using='created_at::timestamptz')
    op.alter_column('patient_insurance', 'verified_at', type_=sa.DateTime(timezone=True), postgresql_using='verified_at::timestamptz')

    # 3. Update visits
    op.alter_column('visits', 'created_at', type_=sa.DateTime(timezone=True), postgresql_using='created_at::timestamptz')
    op.alter_column('visits', 'updated_at', type_=sa.DateTime(timezone=True), postgresql_using='updated_at::timestamptz')

    # 4. Update queues
    op.alter_column('queues', 'created_at', type_=sa.DateTime(timezone=True), postgresql_using='created_at::timestamptz')
    op.alter_column('queues', 'called_at', type_=sa.DateTime(timezone=True), postgresql_using='called_at::timestamptz')
    op.alter_column('queues', 'completed_at', type_=sa.DateTime(timezone=True), postgresql_using='completed_at::timestamptz')


def downgrade() -> None:
    # 1. Downgrade patients
    op.alter_column('patients', 'created_at', type_=sa.DateTime(), postgresql_using='created_at::timestamp')
    op.alter_column('patients', 'updated_at', type_=sa.DateTime(), postgresql_using='updated_at::timestamp')

    # 2. Downgrade patient_insurance
    op.alter_column('patient_insurance', 'created_at', type_=sa.DateTime(), postgresql_using='created_at::timestamp')
    op.alter_column('patient_insurance', 'verified_at', type_=sa.DateTime(), postgresql_using='verified_at::timestamp')

    # 3. Downgrade visits
    op.alter_column('visits', 'created_at', type_=sa.DateTime(), postgresql_using='created_at::timestamp')
    op.alter_column('visits', 'updated_at', type_=sa.DateTime(), postgresql_using='updated_at::timestamp')

    # 4. Downgrade queues
    op.alter_column('queues', 'created_at', type_=sa.DateTime(), postgresql_using='created_at::timestamp')
    op.alter_column('queues', 'called_at', type_=sa.DateTime(), postgresql_using='called_at::timestamp')
    op.alter_column('queues', 'completed_at', type_=sa.DateTime(), postgresql_using='completed_at::timestamp')
