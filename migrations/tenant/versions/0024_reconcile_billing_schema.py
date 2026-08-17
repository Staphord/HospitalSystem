"""Reconcile bills/bill_items schema drift between the two independent
branches that each created these tables (0015_update_consultation_schema
and 0016_ward_module_tables). Both guard their op.create_table() calls with
`if "<table>" not in tables`, so on any given tenant only one of the two
variants actually exists — and neither one, on its own, matches the columns
billing-service's current ORM model reads and writes. This migration
inspects whatever is actually present and brings it in line with that model,
regardless of which of the two variants a given tenant ended up with.

Revision ID: 0024_reconcile_billing_schema
Revises: 0023_merge_tenant_heads
Create Date: 2026-08-13 21:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0024_reconcile_billing_schema"
down_revision: Union[str, None] = "0023_merge_tenant_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "bills" not in tables or "bill_items" not in tables:
        # Neither historical branch has run on this tenant yet; the next
        # fresh provision will get the canonical shape from scratch via
        # 0015/0016's own create_table guards, so there's nothing to fix.
        return

    # --- bills: add whatever the billing-service model expects but either
    # branch's create_table() might be missing ---
    bill_cols = {c["name"] for c in inspector.get_columns("bills")}

    if "admission_id" not in bill_cols:
        op.execute("ALTER TABLE bills ADD COLUMN IF NOT EXISTS admission_id UUID")
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_admission ON bills (admission_id)"
        )
    if "paid_amount" not in bill_cols:
        op.execute(
            "ALTER TABLE bills ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(12, 2) NOT NULL DEFAULT 0"
        )
    if "discount_amount" not in bill_cols:
        op.execute(
            "ALTER TABLE bills ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0"
        )
    if "currency" not in bill_cols:
        op.execute(
            "ALTER TABLE bills ADD COLUMN IF NOT EXISTS currency VARCHAR(5) NOT NULL DEFAULT 'TZS'"
        )
    if "updated_at" not in bill_cols:
        op.execute(
            "ALTER TABLE bills ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_bills_visit_id ON bills (visit_id)")

    # --- bill_items: normalize the primary key column name. The
    # 0016_ward_module_tables variant names it "item_id"; the ORM model
    # (services/billing-service/app/models/billing.py) expects the primary
    # key to be "bill_item_id" with a separate, independent "item_id"
    # column alongside it. ---
    item_cols = {c["name"] for c in inspector.get_columns("bill_items")}
    pk_cols = set(inspector.get_pk_constraint("bill_items").get("constrained_columns") or [])

    if pk_cols == {"item_id"} and "bill_item_id" not in item_cols:
        op.alter_column("bill_items", "item_id", new_column_name="bill_item_id")
        op.execute(
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS item_id UUID DEFAULT gen_random_uuid()"
        )
    elif "bill_item_id" not in item_cols:
        raise RuntimeError(
            "bill_items has neither a bill_item_id nor an item_id primary key column — "
            "this tenant's schema needs manual review before 0024 can proceed."
        )
    elif "item_id" not in item_cols:
        op.execute(
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS item_id UUID DEFAULT gen_random_uuid()"
        )

    item_cols = {c["name"] for c in sa.inspect(conn).get_columns("bill_items")}

    if "item_code" not in item_cols:
        op.execute(
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS item_code VARCHAR(50) NOT NULL DEFAULT 'FEE'"
        )
    if "line_total" not in item_cols:
        op.execute(
            "ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS line_total NUMERIC(12, 2) NOT NULL DEFAULT 0"
        )
        if "total_price" in item_cols:
            op.execute(
                "UPDATE bill_items SET line_total = total_price WHERE total_price IS NOT NULL"
            )
    if "total_price" not in item_cols:
        op.execute("ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS total_price NUMERIC(12, 2)")
    if "source_ref" not in item_cols:
        op.execute("ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS source_ref VARCHAR(100)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_bill_items_bill_id ON bill_items (bill_id)")

    # billing-service relies on (bill_id, item_code, source_ref) uniqueness
    # for idempotent charge application. Add it defensively — if a tenant
    # already has violating duplicate rows, skip with a notice instead of
    # aborting the whole migration; that tenant needs a manual data cleanup.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_bill_items_idempotent'
            ) THEN
                BEGIN
                    ALTER TABLE bill_items
                        ADD CONSTRAINT uq_bill_items_idempotent
                        UNIQUE (bill_id, item_code, source_ref);
                EXCEPTION WHEN unique_violation THEN
                    RAISE NOTICE 'Skipping uq_bill_items_idempotent: duplicate (bill_id, item_code, source_ref) rows present — needs manual cleanup';
                END;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # One-directional reconciliation of divergent historical schemas —
    # there is no single well-defined prior state to restore.
    pass
