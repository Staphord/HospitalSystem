"""Create prescriptions and dispensing tables.

Revision ID: 0014_add_prescriptions_and_dispensing
Revises: 0013_make_timestamps_timezone_aware
Create Date: 2026-07-17 14:00:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_add_prescriptions_and_dispensing"
down_revision: Union[str, None] = "0013_make_timestamps_timezone_aware"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create prescriptions table
    op.create_table(
        "prescriptions",
        sa.Column("prescription_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("prescribed_by", sa.String(length=255), nullable=True),
        sa.Column("prescribed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.PrimaryKeyConstraint("prescription_id"),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_prescriptions_visit_id", "prescriptions", ["visit_id"])
    op.create_index("idx_prescriptions_patient_id", "prescriptions", ["patient_id"])

    # 2. Create prescription_items table
    op.create_table(
        "prescription_items",
        sa.Column("prescription_item_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("prescription_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("drug_name", sa.String(length=200), nullable=False),
        sa.Column("dose", sa.String(length=100), nullable=True),
        sa.Column("frequency", sa.String(length=100), nullable=True),
        sa.Column("duration", sa.String(length=100), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("quantity_prescribed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.PrimaryKeyConstraint("prescription_item_id"),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescriptions.prescription_id"], ondelete="CASCADE"),
    )
    op.create_index("idx_prescription_items_prescription_id", "prescription_items", ["prescription_id"])

    # 3. Create dispensing_records table
    op.create_table(
        "dispensing_records",
        sa.Column("dispensing_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("prescription_item_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity_dispensed", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("batch_number", sa.String(length=100), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("dispensed_by", sa.String(length=255), nullable=True),
        sa.Column("dispensed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("dispensing_id"),
        sa.ForeignKeyConstraint(["prescription_item_id"], ["prescription_items.prescription_item_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.visit_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inventory_id"], ["drug_inventory.inventory_id"], ondelete="SET NULL"),
    )
    op.create_index("idx_dispensing_records_prescription_item_id", "dispensing_records", ["prescription_item_id"])
    op.create_index("idx_dispensing_records_visit_id", "dispensing_records", ["visit_id"])


def downgrade() -> None:
    op.drop_table("dispensing_records")
    op.drop_table("prescription_items")
    op.drop_table("prescriptions")
