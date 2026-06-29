"""Create consultations, diagnoses, and investigation requests.

Revision ID: 0007_create_consultations
Revises: 0006_merge_heads
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_create_consultations"
down_revision: Union[str, None] = "0006_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create consultations table
    op.create_table(
        "consultations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("patient_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("history_of_presenting_illness", sa.Text(), nullable=True),
        sa.Column("examination_findings", sa.Text(), nullable=True),
        sa.Column("clinical_impression", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), server_onupdate=sa.func.now()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_consultations_visit_id", "consultations", ["visit_id"])
    op.create_index("idx_consultations_patient_id", "consultations", ["patient_id"])

    # 2. Create diagnoses table
    op.create_table(
        "diagnoses",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("consultation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("diagnosis_type", sa.String(50), nullable=False),  # "provisional" or "differential"
        sa.Column("code", sa.String(20), nullable=True),  # ICD-10 code (optional)
        sa.Column("description", sa.Text(), nullable=False),  # Description / free text
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_diagnoses_consultation_id", "diagnoses", ["consultation_id"])

    # 3. Create investigation_requests table
    op.create_table(
        "investigation_requests",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("consultation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("request_type", sa.String(50), nullable=False),  # "laboratory" or "radiology"
        sa.Column("test_name", sa.String(255), nullable=False),
        sa.Column("clinical_history", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),  # "pending", "completed", "cancelled"
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_investigation_requests_consultation_id", "investigation_requests", ["consultation_id"])
    op.create_index("idx_investigation_requests_visit_id", "investigation_requests", ["visit_id"])
    op.create_index("idx_investigation_requests_patient_id", "investigation_requests", ["patient_id"])


def downgrade() -> None:
    op.drop_index("idx_investigation_requests_patient_id", table_name="investigation_requests")
    op.drop_index("idx_investigation_requests_visit_id", table_name="investigation_requests")
    op.drop_index("idx_investigation_requests_consultation_id", table_name="investigation_requests")
    op.drop_table("investigation_requests")

    op.drop_index("idx_diagnoses_consultation_id", table_name="diagnoses")
    op.drop_table("diagnoses")

    op.drop_index("idx_consultations_patient_id", table_name="consultations")
    op.drop_index("idx_consultations_visit_id", table_name="consultations")
    op.drop_table("consultations")
