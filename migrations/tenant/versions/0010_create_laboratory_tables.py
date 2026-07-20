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


def _column_type(table: str, column: str, default):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return default, False
    cols = {c["name"]: c for c in inspector.get_columns(table)}
    col = cols.get(column)
    if col is None:
        return default, False
    type_name = type(col["type"]).__name__.lower()
    raw = str(col["type"]).lower()
    if "uuid" in type_name or raw.startswith("uuid"):
        return postgresql.UUID(as_uuid=True), True
    if "int" in type_name:
        return sa.Integer(), True
    length = getattr(col["type"], "length", None) or 50
    return sa.String(length), True


def _patient_ref():
    """Return (type, fk_column) for patients — prefer UUID id, else patient_id varchar."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "patients" not in inspector.get_table_names():
        return postgresql.UUID(as_uuid=True), "id"
    cols = {c["name"]: c for c in inspector.get_columns("patients")}
    id_col = cols.get("id")
    if id_col is not None:
        type_name = type(id_col["type"]).__name__.lower()
        raw = str(id_col["type"]).lower()
        if "uuid" in type_name or raw.startswith("uuid"):
            return postgresql.UUID(as_uuid=True), "id"
    patient_type, _ = _column_type("patients", "patient_id", sa.String(50))
    return patient_type, "patient_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    visit_id_type, visit_ok = _column_type(
        "visits", "visit_id", postgresql.UUID(as_uuid=True)
    )
    patient_id_type, patient_fk_col = _patient_ref()

    if "specimens" not in tables:
        fk_args = [
            sa.ForeignKeyConstraint(
                ["request_id"], ["investigation_requests.id"], ondelete="CASCADE"
            ),
        ]
        if "patients" in tables:
            fk_args.append(
                sa.ForeignKeyConstraint(
                    ["patient_id"],
                    [f"patients.{patient_fk_col}"],
                    ondelete="CASCADE",
                )
            )
        op.create_table(
            "specimens",
            sa.Column("specimen_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", patient_id_type, nullable=False),
            sa.Column("specimen_type", sa.String(length=100), nullable=False),
            sa.Column("collection_site", sa.String(length=100), nullable=True),
            sa.Column("collected_by", sa.String(length=255), nullable=False),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default="collected",
            ),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            *fk_args,
        )
        op.create_index("idx_specimens_request_id", "specimens", ["request_id"])
        op.create_index("idx_specimens_patient_id", "specimens", ["patient_id"])

    if "lab_results" not in tables:
        fk_args = [
            sa.ForeignKeyConstraint(
                ["request_id"], ["investigation_requests.id"], ondelete="CASCADE"
            ),
        ]
        if visit_ok and "visits" in tables:
            fk_args.append(
                sa.ForeignKeyConstraint(
                    ["visit_id"], ["visits.visit_id"], ondelete="CASCADE"
                )
            )
        if "patients" in tables:
            fk_args.append(
                sa.ForeignKeyConstraint(
                    ["patient_id"],
                    [f"patients.{patient_fk_col}"],
                    ondelete="CASCADE",
                )
            )
        op.create_table(
            "lab_results",
            sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("visit_id", visit_id_type, nullable=False),
            sa.Column("patient_id", patient_id_type, nullable=False),
            sa.Column("specimen_type", sa.String(length=100), nullable=False),
            sa.Column("result_value", sa.Text(), nullable=False),
            sa.Column("unit", sa.String(length=50), nullable=True),
            sa.Column("reference_range", sa.String(length=100), nullable=True),
            sa.Column(
                "is_critical",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("result_notes", sa.Text(), nullable=True),
            sa.Column("performed_by", sa.String(length=255), nullable=False),
            sa.Column("verified_by", sa.String(length=255), nullable=True),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default="resulted",
            ),
            sa.Column("resulted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "critical_notified_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            *fk_args,
        )
        op.create_index("idx_lab_results_request_id", "lab_results", ["request_id"])
        op.create_index("idx_lab_results_visit_id", "lab_results", ["visit_id"])
        op.create_index("idx_lab_results_patient_id", "lab_results", ["patient_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "lab_results" in tables:
        op.drop_index("idx_lab_results_patient_id", table_name="lab_results")
        op.drop_index("idx_lab_results_visit_id", table_name="lab_results")
        op.drop_index("idx_lab_results_request_id", table_name="lab_results")
        op.drop_table("lab_results")

    if "specimens" in tables:
        op.drop_index("idx_specimens_patient_id", table_name="specimens")
        op.drop_index("idx_specimens_request_id", table_name="specimens")
        op.drop_table("specimens")
