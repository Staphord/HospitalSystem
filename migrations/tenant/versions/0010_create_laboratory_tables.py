"""Create laboratory tables: specimens and lab_results.

Revision ID: 0010_create_laboratory_tables
Revises: 0009_merge_radiology_and_consultation
Create Date: 2026-07-08 12:00:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_create_laboratory_tables"
down_revision: Union[str, None] = "0009_merge_radiology_and_consultation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create specimens table
    op.create_table(
        "specimens",
        sa.Column("specimen_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specimen_type", sa.String(length=100), nullable=False),
        sa.Column("collection_site", sa.String(length=100), nullable=True),
        sa.Column("collected_by", sa.String(length=255), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="collected"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["request_id"], ["investigation_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_specimens_request_id", "specimens", ["request_id"])
    op.create_index("idx_specimens_patient_id", "specimens", ["patient_id"])

    # 2. Create lab_results table
    op.create_table(
        "lab_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specimen_type", sa.String(length=100), nullable=False),
        sa.Column("result_value", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("reference_range", sa.String(length=100), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_notes", sa.Text(), nullable=True),
        sa.Column("performed_by", sa.String(length=255), nullable=False),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="resulted"),
        sa.Column("resulted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("critical_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["request_id"], ["investigation_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_lab_results_request_id", "lab_results", ["request_id"])
    op.create_index("idx_lab_results_visit_id", "lab_results", ["visit_id"])
    op.create_index("idx_lab_results_patient_id", "lab_results", ["patient_id"])


def downgrade() -> None:
    op.drop_index("idx_lab_results_patient_id", table_name="lab_results")
    op.drop_index("idx_lab_results_visit_id", table_name="lab_results")
    op.drop_index("idx_lab_results_request_id", table_name="lab_results")
    op.drop_table("lab_results")

    op.drop_index("idx_specimens_patient_id", table_name="specimens")
    op.drop_index("idx_specimens_request_id", table_name="specimens")
    op.drop_table("specimens")
