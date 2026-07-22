"""Update consultation schemas, prescriptions, and basic billing tables.

Revision ID: 0015_update_consultation_schema
Revises: 0014_align_triage_assessments
Create Date: 2026-07-15 20:10:00.000000+03:00
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0015_update_consultation_schema"
down_revision: Union[str, None] = "0014_align_triage_assessments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update consultations
    op.add_column("consultations", sa.Column("consultation_status", sa.String(50), nullable=False, server_default="in_progress"))
    op.add_column("consultations", sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("consultations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("consultations", sa.Column("disposition", sa.String(50), nullable=True))
    op.add_column("consultations", sa.Column("referral_type", sa.String(50), nullable=True))
    op.add_column("consultations", sa.Column("referral_notes", sa.Text(), nullable=True))

    # 2. Update diagnoses
    op.add_column("diagnoses", sa.Column("sequence_order", sa.Integer(), nullable=True))
    op.add_column("diagnoses", sa.Column("recorded_by", sa.String(255), nullable=True))
    op.add_column("diagnoses", sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))

    # 3. Update investigation_requests
    op.add_column("investigation_requests", sa.Column("test_code", sa.String(50), nullable=True))
    op.add_column("investigation_requests", sa.Column("requested_by", sa.String(255), nullable=True))
    op.add_column("investigation_requests", sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))

    # 4. Create prescriptions table
    op.create_table(
        "prescriptions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("consultation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("drug_name", sa.String(200), nullable=False),
        sa.Column("dose", sa.String(50), nullable=False),
        sa.Column("frequency", sa.String(50), nullable=False),
        sa.Column("duration", sa.String(50), nullable=False),
        sa.Column("route", sa.String(50), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("prescribed_by", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("prescribed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_prescriptions_visit_id", "prescriptions", ["visit_id"])
    op.create_index("idx_prescriptions_consultation_id", "prescriptions", ["consultation_id"])

    # 5. Create fee_schedules table
    op.create_table(
        "fee_schedules",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_code", sa.String(50), nullable=True),
        sa.Column("item_name", sa.String(200), nullable=False),
        sa.Column("item_type", sa.String(50), nullable=False), # "consultation", "lab", "radiology"
        sa.Column("standard_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("insurance_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("effective_from", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("effective_to", sa.Date(), nullable=True),
    )
    op.create_index("idx_fee_schedules_item_code", "fee_schedules", ["item_code"])

    # 6. Seed mock entries into fee_schedules
    op.execute("""
        INSERT INTO fee_schedules (id, item_code, item_name, item_type, standard_price, insurance_price, is_active, effective_from)
        VALUES 
            (gen_random_uuid(), 'CONS-001', 'Consultation Fee', 'consultation', 1000.0, 800.0, true, CURRENT_DATE),
            (gen_random_uuid(), 'LAB-001', 'Malaria BS', 'lab', 500.0, 400.0, true, CURRENT_DATE),
            (gen_random_uuid(), 'RAD-001', 'Chest X-Ray', 'radiology', 1500.0, 1200.0, true, CURRENT_DATE),
            (gen_random_uuid(), 'LAB-002', 'CBC (Complete Blood Count)', 'lab', 750.0, 600.0, true, CURRENT_DATE)
        ON CONFLICT DO NOTHING;
    """)

    # 7. Create bills table
    op.create_table(
        "bills",
        sa.Column("bill_id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_bills_visit_id", "bills", ["visit_id"])

    # 8. Create bill_items table
    op.create_table(
        "bill_items",
        sa.Column("bill_item_id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bill_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(50), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("reference_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.bill_id"], ondelete="CASCADE"),
    )
    op.create_index("idx_bill_items_bill_id", "bill_items", ["bill_id"])


def downgrade() -> None:
    # Drop bill_items and bills
    op.drop_table("bill_items")
    op.drop_table("bills")
    
    # Drop fee_schedules
    op.drop_table("fee_schedules")

    # Drop prescriptions
    op.drop_table("prescriptions")

    # Remove columns from investigation_requests
    op.drop_column("investigation_requests", "requested_at")
    op.drop_column("investigation_requests", "requested_by")
    op.drop_column("investigation_requests", "test_code")

    # Remove columns from diagnoses
    op.drop_column("diagnoses", "recorded_at")
    op.drop_column("diagnoses", "recorded_by")
    op.drop_column("diagnoses", "sequence_order")

    # Remove columns from consultations
    op.drop_column("consultations", "referral_notes")
    op.drop_column("consultations", "referral_type")
    op.drop_column("consultations", "disposition")
    op.drop_column("consultations", "completed_at")
    op.drop_column("consultations", "started_at")
    op.drop_column("consultations", "consultation_status")
