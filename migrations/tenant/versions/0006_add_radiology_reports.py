"""Add radiology_reports table.

Revision ID: 0006_add_radiology_reports
Revises: 0005_align_patients_table_with_model
Create Date: 2024-06-29 00:00:00.000000+00:00

Creates the radiology_reports table for storing imaging study results.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_add_radiology_reports"
down_revision: Union[str, None] = "0005_align_patients_table_with_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE modality_enum AS ENUM (
                'xray', 'ct', 'mri', 'ultrasound', 'fluoroscopy', 'mammography', 'other'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE report_status_enum AS ENUM (
                'scheduled', 'performed', 'reported', 'verified'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.create_table(
        "radiology_reports",
        sa.Column("report_id", sa.UUID, nullable=False),
        sa.Column("request_id", sa.UUID, nullable=True),
        sa.Column("visit_id", sa.UUID, nullable=False),
        sa.Column("patient_id", sa.UUID, nullable=False),
        sa.Column("modality", sa.Enum("xray", "ct", "mri", "ultrasound", "fluoroscopy", "mammography", "other", name="modality_enum"), nullable=False),
        sa.Column("body_part", sa.String(100), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("findings", sa.Text, nullable=True),
        sa.Column("impression", sa.Text, nullable=True),
        sa.Column("image_reference", sa.String(255), nullable=True),
        sa.Column("performed_by", sa.UUID, nullable=False),
        sa.Column("reported_by", sa.UUID, nullable=True),
        sa.Column("status", sa.Enum("scheduled", "performed", "reported", "verified", name="report_status_enum"), nullable=False, server_default="scheduled"),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("report_id"),
    )

    op.create_index("idx_radiology_reports_visit", "radiology_reports", ["visit_id"])
    op.create_index("idx_radiology_reports_patient", "radiology_reports", ["patient_id"])
    op.create_index("idx_radiology_reports_status", "radiology_reports", ["status"])


def downgrade() -> None:
    op.drop_table("radiology_reports")
    op.execute("DROP TYPE IF EXISTS report_status_enum")
    op.execute("DROP TYPE IF EXISTS modality_enum")