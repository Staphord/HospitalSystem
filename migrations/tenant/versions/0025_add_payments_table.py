"""Add payments table.

billing-service's Payment ORM model and its POST /bills/{bill_id}/payments
endpoint (services/billing-service/app/api/v1/router.py) have depended on a
`payments` table since that service was built out, but no migration ever
created it — a real, currently-live gap found while auditing the tenant
schema coverage the README claimed for this table.

Revision ID: 0025_add_payments_table
Revises: 0024_reconcile_billing_schema
Create Date: 2026-08-13 21:15:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_add_payments_table"
down_revision: Union[str, None] = "0024_reconcile_billing_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "payments" in inspector.get_table_names():
        return

    op.create_table(
        "payments",
        sa.Column("payment_id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bill_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_method", sa.String(32), nullable=False, server_default="Cash"),
        sa.Column("receipt_number", sa.String(64), nullable=False, unique=True),
        sa.Column("cashier_id", sa.String(100), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.bill_id"], ondelete="CASCADE"),
    )
    op.create_index("idx_payments_bill_id", "payments", ["bill_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "payments" in inspector.get_table_names():
        op.drop_table("payments")
