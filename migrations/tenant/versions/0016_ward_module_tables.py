"""Ward / inpatient tables + consultation disposition + minimal bills.

Revision ID: 0016_ward_module_tables
Revises: 0015_align_legacy_admin_columns
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_ward_module_tables"
down_revision: Union[str, None] = "0015_align_legacy_admin_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _extend_visit_status_enum() -> None:
    """ADD VALUE cannot safely run inside the Alembic transaction.

    A failure there aborts the connection (InFailedSqlTransaction) even if
    Python catches the exception. Use autocommit and only when the type exists.
    """
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'visit_status_enum'")
        ).scalar()
        if not exists:
            return
        for value in ("admitted", "discharged"):
            conn.execute(
                sa.text(
                    f"ALTER TYPE visit_status_enum ADD VALUE IF NOT EXISTS '{value}'"
                )
            )


def upgrade() -> None:
    # Enum first (separate commit) so a failure cannot poison the main migration txn.
    try:
        _extend_visit_status_enum()
    except Exception:
        # Soft-fail: ward still works; visit status soft-updates may be skipped.
        pass

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "consultations" in tables:
        cols = {c["name"] for c in inspector.get_columns("consultations")}
        if "disposition" not in cols:
            op.add_column(
                "consultations",
                sa.Column("disposition", sa.String(50), nullable=True),
            )
        if "disposition_notes" not in cols:
            op.add_column(
                "consultations",
                sa.Column("disposition_notes", sa.Text(), nullable=True),
            )

    if "admissions" not in tables:
        op.create_table(
            "admissions",
            sa.Column("admission_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("bed_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("admitting_doctor_id", sa.String(255), nullable=False),
            sa.Column("admitting_diagnosis", sa.Text(), nullable=False),
            sa.Column(
                "admission_date",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("discharge_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("length_of_stay_days", sa.Numeric(6, 1), nullable=True),
            sa.Column("discharge_diagnosis", sa.Text(), nullable=True),
            sa.Column("discharge_instructions", sa.Text(), nullable=True),
            sa.Column("discharge_order_by", sa.String(255), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("ward_name", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_admissions_visit", "admissions", ["visit_id"])
        op.create_index("idx_admissions_patient", "admissions", ["patient_id"])
        op.create_index("idx_admissions_bed", "admissions", ["bed_id"])
        op.create_index("idx_admissions_status", "admissions", ["status"])

    if "inpatient_orders" not in tables:
        op.create_table(
            "inpatient_orders",
            sa.Column("order_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("admission_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("order_type", sa.String(50), nullable=False),
            sa.Column("order_detail", sa.Text(), nullable=False),
            sa.Column("frequency", sa.String(50), nullable=True),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("ordered_by", sa.String(255), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column(
                "ordered_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_inpatient_orders_admission", "inpatient_orders", ["admission_id"])

    if "nursing_notes" not in tables:
        op.create_table(
            "nursing_notes",
            sa.Column("note_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("admission_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("note_type", sa.String(50), nullable=False),
            sa.Column("note_text", sa.Text(), nullable=False),
            sa.Column("vitals_bp", sa.String(20), nullable=True),
            sa.Column("vitals_temp", sa.Numeric(5, 2), nullable=True),
            sa.Column("vitals_pulse", sa.Integer(), nullable=True),
            sa.Column("vitals_spo2", sa.Numeric(5, 2), nullable=True),
            sa.Column("authored_by", sa.String(255), nullable=False),
            sa.Column(
                "authored_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_nursing_notes_admission", "nursing_notes", ["admission_id"])

    if "bills" not in tables:
        op.create_table(
            "bills",
            sa.Column("bill_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("admission_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="open"),
            sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(5), nullable=False, server_default="USD"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_bills_admission", "bills", ["admission_id"], unique=False)
        op.create_index("idx_bills_visit", "bills", ["visit_id"])

    if "bill_items" not in tables:
        op.create_table(
            "bill_items",
            sa.Column("item_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("bill_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("item_code", sa.String(50), nullable=False),
            sa.Column("item_type", sa.String(50), nullable=False),
            sa.Column("description", sa.String(255), nullable=False),
            sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
            sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
            sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
            sa.Column("source_ref", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "bill_id", "item_code", "source_ref", name="uq_bill_items_idempotent"
            ),
        )
        op.create_index("idx_bill_items_bill", "bill_items", ["bill_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    for table in ("bill_items", "bills", "nursing_notes", "inpatient_orders", "admissions"):
        if table in tables:
            op.drop_table(table)
    if "consultations" in tables:
        cols = {c["name"] for c in inspector.get_columns("consultations")}
        if "disposition_notes" in cols:
            op.drop_column("consultations", "disposition_notes")
        if "disposition" in cols:
            op.drop_column("consultations", "disposition")
