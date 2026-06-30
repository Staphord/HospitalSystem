"""Add pharmacy inventory tables.

Revision ID: 0007_add_pharmacy_inventory
Revises: 0006_add_radiology_reports
Create Date: 2026-06-29 00:00:00.000000+00:00

Creates drug_inventory and drug_inventory_transactions for pharmacy stock management.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_add_pharmacy_inventory"
down_revision: Union[str, None] = "0006_add_radiology_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drug_inventory",
        sa.Column("inventory_id", sa.UUID(), nullable=False),
        sa.Column("drug_name", sa.String(200), nullable=False),
        sa.Column("brand_name", sa.String(200), nullable=True),
        sa.Column("drug_code", sa.String(50), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(50), nullable=False, server_default="tablets"),
        sa.Column("quantity_in_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reorder_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("inventory_id"),
    )
    op.create_index("idx_drug_inventory_drug_name", "drug_inventory", ["drug_name"])
    op.create_index("idx_drug_inventory_category", "drug_inventory", ["category"])

    op.create_table(
        "drug_inventory_transactions",
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("inventory_id", sa.UUID(), nullable=False),
        sa.Column("transaction_type", sa.String(30), nullable=False),
        sa.Column("quantity_change", sa.Integer(), nullable=False),
        sa.Column("quantity_before", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("batch_number", sa.String(100), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("performed_by", sa.String(255), nullable=False),
        sa.Column("performed_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index(
        "idx_drug_inventory_transactions_inventory",
        "drug_inventory_transactions",
        ["inventory_id"],
    )
    op.create_index(
        "idx_drug_inventory_transactions_created",
        "drug_inventory_transactions",
        ["created_at"],
    )

    op.execute("""
        INSERT INTO drug_inventory (
            inventory_id, drug_name, brand_name, drug_code, category, unit,
            quantity_in_stock, reorder_level, unit_cost, unit_price, location, is_active
        ) VALUES
        (
            'e5000005-0005-4005-8005-000000000005',
            'Amoxicillin', 'Amoxil', 'AMX-500', 'Antibiotic', 'tablets',
            179, 100, 50.00, 80.00, 'Shelf B-3', true
        ),
        (
            'e5000005-0005-4005-8005-000000000099',
            'Metronidazole', 'Flagyl', 'MTZ-400', 'Antibiotic', 'tablets',
            12, 50, 30.00, 55.00, 'Shelf C-1', true
        )
        ON CONFLICT (inventory_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("drug_inventory_transactions")
    op.drop_table("drug_inventory")
