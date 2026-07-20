"""Align triage_assessments table with specification.

Revision ID: 0014_align_triage_assessments
Revises: 0013_make_timestamps_timezone_aware
Create Date: 2026-07-14 23:05:00.000000+03:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014_align_triage_assessments"
down_revision: Union[str, None] = "0013_make_timestamps_timezone_aware"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Rename column id -> triage_id
    op.alter_column('triage_assessments', 'id', new_column_name='triage_id')
    
    # 2. Add triage_nurse_id
    op.add_column('triage_assessments', sa.Column('triage_nurse_id', sa.UUID(as_uuid=True), nullable=False))
    
    # 3. Drop blood_pressure and add systolic/diastolic
    op.drop_column('triage_assessments', 'blood_pressure')
    op.add_column('triage_assessments', sa.Column('blood_pressure_systolic', sa.Integer(), nullable=True))
    op.add_column('triage_assessments', sa.Column('blood_pressure_diastolic', sa.Integer(), nullable=True))
    
    # 4. Rename pulse -> pulse_rate
    op.alter_column('triage_assessments', 'pulse', new_column_name='pulse_rate')
    
    # 5. Rename weight -> weight_kg
    op.alter_column('triage_assessments', 'weight', new_column_name='weight_kg')
    
    # 6. Rename presenting_complaint -> chief_complaint and make it NOT NULL
    op.alter_column('triage_assessments', 'presenting_complaint', new_column_name='chief_complaint', nullable=False)
    
    # 7. Rename structured_complaint -> complaint_code and alter type to VARCHAR(20)
    op.alter_column('triage_assessments', 'structured_complaint', new_column_name='complaint_code', type_=sa.String(20))
    
    # 8. Rename notes -> triage_notes
    op.alter_column('triage_assessments', 'notes', new_column_name='triage_notes')
    
    # 9. Add assessed_at
    op.add_column('triage_assessments', sa.Column('assessed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    
    # 10. Change patient_id type to UUID
    op.alter_column('triage_assessments', 'patient_id', type_=sa.UUID(as_uuid=True), postgresql_using='patient_id::uuid', nullable=False)


def downgrade() -> None:
    # 10. Revert patient_id type
    op.alter_column('triage_assessments', 'patient_id', type_=sa.String(50), postgresql_using='patient_id::varchar', nullable=False)
    
    # 9. Drop assessed_at
    op.drop_column('triage_assessments', 'assessed_at')
    
    # 8. Rename triage_notes -> notes
    op.alter_column('triage_assessments', 'triage_notes', new_column_name='notes')
    
    # 7. Rename complaint_code -> structured_complaint and alter type to VARCHAR(255)
    op.alter_column('triage_assessments', 'complaint_code', new_column_name='structured_complaint', type_=sa.String(255))
    
    # 6. Rename chief_complaint -> presenting_complaint and make it nullable
    op.alter_column('triage_assessments', 'chief_complaint', new_column_name='presenting_complaint', nullable=True)
    
    # 5. Rename weight_kg -> weight
    op.alter_column('triage_assessments', 'weight_kg', new_column_name='weight')
    
    # 4. Rename pulse_rate -> pulse
    op.alter_column('triage_assessments', 'pulse_rate', new_column_name='pulse')
    
    # 3. Drop systolic/diastolic and add blood_pressure
    op.drop_column('triage_assessments', 'blood_pressure_diastolic')
    op.drop_column('triage_assessments', 'blood_pressure_systolic')
    op.add_column('triage_assessments', sa.Column('blood_pressure', sa.String(20), nullable=True))
    
    # 2. Drop triage_nurse_id
    op.drop_column('triage_assessments', 'triage_nurse_id')
    
    # 1. Rename triage_id -> id
    op.alter_column('triage_assessments', 'triage_id', new_column_name='id')
