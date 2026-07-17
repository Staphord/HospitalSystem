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


def _column_type(table: str, column: str, default):
    """Return a SQLAlchemy type matching an existing column, or default."""
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
        return sa.UUID(as_uuid=True), True
    if "int" in type_name:
        return sa.Integer(), True
    length = getattr(col["type"], "length", None) or 50
    return sa.String(length), True


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    visit_id_type, visit_ok = _column_type("visits", "visit_id", sa.UUID(as_uuid=True))
    # Prefer FK to patients.id when types are compatible; else use patients.patient_id as String.
    patient_id_type, patient_id_ok = _column_type("patients", "id", sa.UUID(as_uuid=True))
    patient_fk_col = "id"
    # Legacy tenants: patients.id is INTEGER while app models use UUID/string identifiers.
    # Prefer patients.patient_id (varchar) when id is not UUID.
    if patient_id_ok:
        type_name = type(
            {c["name"]: c for c in inspector.get_columns("patients")}["id"]["type"]
        ).__name__.lower()
        raw = str(
            {c["name"]: c for c in inspector.get_columns("patients")}["id"]["type"]
        ).lower()
        if "uuid" not in type_name and not raw.startswith("uuid"):
            patient_id_type, _ = _column_type(
                "patients", "patient_id", sa.String(50)
            )
            patient_fk_col = "patient_id"

    if "consultations" not in tables:
        fk_args = []
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
            "consultations",
            sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
            sa.Column("visit_id", visit_id_type, nullable=False, unique=True),
            sa.Column("patient_id", patient_id_type, nullable=False),
            sa.Column("history_of_presenting_illness", sa.Text(), nullable=True),
            sa.Column("examination_findings", sa.Text(), nullable=True),
            sa.Column("clinical_impression", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
                server_onupdate=sa.func.now(),
            ),
            sa.Column("created_by", sa.String(255), nullable=True),
            *fk_args,
        )
        op.create_index("idx_consultations_visit_id", "consultations", ["visit_id"])
        op.create_index(
            "idx_consultations_patient_id", "consultations", ["patient_id"]
        )

    if "diagnoses" not in tables:
        op.create_table(
            "diagnoses",
            sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
            sa.Column("consultation_id", sa.UUID(as_uuid=True), nullable=False),
            sa.Column("diagnosis_type", sa.String(50), nullable=False),
            sa.Column("code", sa.String(20), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["consultation_id"], ["consultations.id"], ondelete="CASCADE"
            ),
        )
        op.create_index(
            "idx_diagnoses_consultation_id", "diagnoses", ["consultation_id"]
        )

    if "investigation_requests" not in tables:
        fk_args = [
            sa.ForeignKeyConstraint(
                ["consultation_id"], ["consultations.id"], ondelete="CASCADE"
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
            "investigation_requests",
            sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
            sa.Column("consultation_id", sa.UUID(as_uuid=True), nullable=False),
            sa.Column("visit_id", visit_id_type, nullable=False),
            sa.Column("patient_id", patient_id_type, nullable=False),
            sa.Column("request_type", sa.String(50), nullable=False),
            sa.Column("test_name", sa.String(255), nullable=False),
            sa.Column("clinical_history", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(50),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("created_by", sa.String(255), nullable=True),
            *fk_args,
        )
        op.create_index(
            "idx_investigation_requests_consultation_id",
            "investigation_requests",
            ["consultation_id"],
        )
        op.create_index(
            "idx_investigation_requests_visit_id",
            "investigation_requests",
            ["visit_id"],
        )
        op.create_index(
            "idx_investigation_requests_patient_id",
            "investigation_requests",
            ["patient_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "investigation_requests" in tables:
        op.drop_index(
            "idx_investigation_requests_patient_id",
            table_name="investigation_requests",
        )
        op.drop_index(
            "idx_investigation_requests_visit_id",
            table_name="investigation_requests",
        )
        op.drop_index(
            "idx_investigation_requests_consultation_id",
            table_name="investigation_requests",
        )
        op.drop_table("investigation_requests")

    if "diagnoses" in tables:
        op.drop_index("idx_diagnoses_consultation_id", table_name="diagnoses")
        op.drop_table("diagnoses")

    if "consultations" in tables:
        op.drop_index("idx_consultations_patient_id", table_name="consultations")
        op.drop_index("idx_consultations_visit_id", table_name="consultations")
        op.drop_table("consultations")
