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
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', sa.String(50), nullable=False),
        sa.Column('patient_number', sa.String(30), nullable=False),
        sa.Column('full_name', sa.String(200), nullable=False),
        sa.Column('date_of_birth', sa.Date, nullable=False),
        sa.Column('gender', sa.String(20), nullable=False),
        sa.Column('phone', sa.String(30)),
        sa.Column('email', sa.String(150)),
        sa.Column('address', sa.Text),
        sa.Column('emergency_contact_name', sa.String(200)),
        sa.Column('emergency_contact_phone', sa.String(30)),
        sa.Column('national_id', sa.String(50)),
        sa.Column('medical_history', sa.Text),
        sa.Column('allergies', sa.Text),
        sa.Column('blood_group', sa.String(10)),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), server_onupdate=sa.func.now()),
        sa.Column('created_by', sa.String(200)),
        sa.UniqueConstraint('hospital_id', 'patient_number', name='uq_hospital_patient_number'),
        sa.UniqueConstraint('hospital_id', 'national_id', name='uq_hospital_national_id'),
    )
    
    # Create patient_number_sequences table
    op.create_table(
        'patient_number_sequences',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('hospital_id', sa.String(50), nullable=False),
        sa.Column('date_key', sa.String(8), nullable=False),
        sa.Column('counter', sa.Integer, nullable=False, server_default='0'),
        sa.UniqueConstraint('hospital_id', 'date_key', name='uq_hospital_date_key'),
    )
    
    # Create patient_insurance table
    op.create_table(
        'patient_insurance',
        sa.Column('insurance_id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('patient_id', sa.String(36), nullable=False),
        sa.Column('insurer_name', sa.String(150), nullable=False),
        sa.Column('policy_number', sa.String(100), nullable=False),
        sa.Column('coverage_limit', sa.Numeric(12, 2)),
        sa.Column('expiry_date', sa.Date),
        sa.Column('verification_status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    
    # Create visits table
    op.create_table(
        'visits',
        sa.Column('visit_id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('patient_id', sa.String(36), nullable=False),
        sa.Column('visit_number', sa.String(20), nullable=False, unique=True),
        sa.Column('visit_date', sa.Date, nullable=False, server_default=sa.func.current_date()),
        sa.Column('visit_type', sa.String(50), nullable=False),
        sa.Column('payment_type', sa.String(50), nullable=False),
        sa.Column('insurance_id', sa.UUID(as_uuid=True), sa.ForeignKey('patient_insurance.insurance_id')),
        sa.Column('verification_flag', sa.Text),
        sa.Column('queue_number', sa.String(10)),
        sa.Column('status', sa.String(50), nullable=False, server_default='registered'),
        sa.Column('registered_by', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), server_onupdate=sa.func.now()),
    )
    
    # Create queues table
    op.create_table(
        'queues',
        sa.Column('queue_id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('visit_id', sa.UUID(as_uuid=True), sa.ForeignKey('visits.visit_id'), nullable=False),
        sa.Column('patient_id', sa.String(36), nullable=False),
        sa.Column('queue_type', sa.String(50), nullable=False),
        sa.Column('queue_number', sa.String(10), nullable=False),
        sa.Column('priority', sa.String(50), nullable=False, server_default='non_urgent'),
        sa.Column('status', sa.String(50), nullable=False, server_default='waiting'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    
    # Create visit_number_sequences table
    op.create_table(
        'visit_number_sequences',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('date_key', sa.String(8), nullable=False, unique=True),
        sa.Column('counter', sa.Integer, nullable=False, server_default='0'),
    )
    
    # Create queue_number_sequences table
    op.create_table(
        'queue_number_sequences',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('queue_type', sa.String(20), nullable=False),
        sa.Column('date_key', sa.String(8), nullable=False),
        sa.Column('counter', sa.Integer, nullable=False, server_default='0'),
        sa.UniqueConstraint('queue_type', 'date_key', name='uq_queue_type_date_key'),
    )
    
    # Create appointments table
    op.create_table(
        'appointments',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('appointment_id', sa.String(50), nullable=False, unique=True),
        sa.Column('patient_id', sa.UUID(as_uuid=True), sa.ForeignKey('patients.id'), nullable=False),
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
    op.create_index('idx_patients_hospital_id', 'patients', ['hospital_id'])
    op.create_index('idx_patients_patient_number', 'patients', ['patient_number'])
    op.create_index('idx_patients_full_name', 'patients', ['full_name'])
    op.create_index('idx_sequences_hospital_date', 'patient_number_sequences', ['hospital_id', 'date_key'])
    op.create_index('idx_insurance_patient', 'patient_insurance', ['patient_id'])
    op.create_index('idx_visits_patient', 'visits', ['patient_id'])
    op.create_index('idx_visits_number', 'visits', ['visit_number'])
    op.create_index('idx_queues_visit', 'queues', ['visit_id'])
    op.create_index('idx_appointments_patient_id', 'appointments', ['patient_id'])
    op.create_index('idx_appointments_date', 'appointments', ['appointment_date'])


def downgrade() -> None:
    op.drop_table('appointments')
    op.drop_table('queues')
    op.drop_table('visits')
    op.drop_table('patient_insurance')
    op.drop_table('patient_number_sequences')
    op.drop_table('patients')
    op.drop_table('visit_number_sequences')
    op.drop_table('queue_number_sequences')
    op.drop_table('users')
