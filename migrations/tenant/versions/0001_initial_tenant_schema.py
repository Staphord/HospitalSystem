"""Initial tenant schema migration.

Revision ID: 0001_initial_tenant_schema
Revises: 
Create Date: 2024-01-01 00:00:00.000000+00:00

Tenant databases contain all clinical tables for a specific hospital.
This initial revision creates the users table and other common tenant tables.

Tables created:
- users: Hospital staff and patients (tenant-specific)
- patients: Patient records
- visits: Patient visit records
- appointments: Appointment scheduling
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_tenant_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table for tenant-specific users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('keycloak_sub', sa.String(255), nullable=False, unique=True),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('full_name', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', sa.String(50), nullable=True),
        sa.Column('hospital_id', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # Create patients table
    op.create_table(
        'patients',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('patient_id', sa.String(50), nullable=False, unique=True),
        sa.Column('full_name', sa.String(255), nullable=False),
        sa.Column('date_of_birth', sa.Date, nullable=True),
        sa.Column('gender', sa.String(20), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('address', sa.Text, nullable=True),
        sa.Column('emergency_contact', sa.String(255), nullable=True),
        sa.Column('blood_type', sa.String(10), nullable=True),
        sa.Column('allergies', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # Create visits table
    op.create_table(
        'visits',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('visit_id', sa.String(50), nullable=False, unique=True),
        sa.Column('patient_id', sa.String(50), sa.ForeignKey('patients.patient_id'), nullable=False),
        sa.Column('doctor_id', sa.String(255), nullable=True),
        sa.Column('department', sa.String(100), nullable=True),
        sa.Column('status', sa.String(50), default='pending'),
        sa.Column('priority', sa.String(20), default='normal'),
        sa.Column('check_in_time', sa.DateTime, default=sa.func.now()),
        sa.Column('check_out_time', sa.DateTime, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # Create appointments table
    op.create_table(
        'appointments',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('appointment_id', sa.String(50), nullable=False, unique=True),
        sa.Column('patient_id', sa.String(50), sa.ForeignKey('patients.patient_id'), nullable=False),
        sa.Column('doctor_id', sa.String(255), nullable=True),
        sa.Column('appointment_date', sa.DateTime, nullable=False),
        sa.Column('status', sa.String(50), default='scheduled'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # Create indexes
    op.create_index('idx_users_keycloak_sub', 'users', ['keycloak_sub'])
    op.create_index('idx_users_hospital_id', 'users', ['hospital_id'])
    op.create_index('idx_users_email', 'users', ['email'])
    op.create_index('idx_patients_patient_id', 'patients', ['patient_id'])
    op.create_index('idx_visits_patient_id', 'visits', ['patient_id'])
    op.create_index('idx_visits_status', 'visits', ['status'])
    op.create_index('idx_appointments_patient_id', 'appointments', ['patient_id'])
    op.create_index('idx_appointments_date', 'appointments', ['appointment_date'])


def downgrade() -> None:
    op.drop_table('appointments')
    op.drop_table('visits')
    op.drop_table('patients')
    op.drop_table('users')
