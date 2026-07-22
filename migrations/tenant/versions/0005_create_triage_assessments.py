"""Create triage_assessments table.

Revision ID: 0005_create_triage_assessments
Revises: 0004_add_user_mfa_fields
Create Date: 2026-06-26 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_create_triage_assessments"
down_revision: Union[str, None] = "0004_add_user_mfa_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _visit_id_column_type():
    """Match visits.visit_id type (UUID on new tenants, VARCHAR on legacy)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "visits" not in inspector.get_table_names():
        return sa.UUID(as_uuid=True), True
    cols = {c["name"]: c for c in inspector.get_columns("visits")}
    visit_col = cols.get("visit_id")
    if visit_col is None:
        return sa.UUID(as_uuid=True), True
    type_name = type(visit_col["type"]).__name__.lower()
    raw = str(visit_col["type"]).lower()
    if "uuid" in type_name or raw.startswith("uuid"):
        return sa.UUID(as_uuid=True), True
    # Legacy varchar/character varying visit_id
    length = getattr(visit_col["type"], "length", None) or 50
    return sa.String(length), True


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "triage_assessments" in inspector.get_table_names():
        return

    visit_id_type, add_fk = _visit_id_column_type()
    fk_args = []
    if add_fk and "visits" in inspector.get_table_names():
        fk_args.append(
            sa.ForeignKeyConstraint(
                ["visit_id"], ["visits.visit_id"], ondelete="CASCADE"
            )
        )

    op.create_table(
        "triage_assessments",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("visit_id", visit_id_type, nullable=False, unique=True),
        sa.Column("patient_id", sa.String(50), nullable=False),
        sa.Column("blood_pressure", sa.String(20), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("pulse", sa.Integer(), nullable=True),
        sa.Column("oxygen_saturation", sa.Float(), nullable=True),
        sa.Column("respiratory_rate", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("presenting_complaint", sa.Text(), nullable=True),
        sa.Column("structured_complaint", sa.String(255), nullable=True),
        sa.Column("triage_category", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
    op.create_index(
        "idx_triage_assessments_visit_id", "triage_assessments", ["visit_id"]
    )
    op.create_index(
        "idx_triage_assessments_patient_id", "triage_assessments", ["patient_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "triage_assessments" not in inspector.get_table_names():
        return
    op.drop_index("idx_triage_assessments_patient_id", table_name="triage_assessments")
    op.drop_index("idx_triage_assessments_visit_id", table_name="triage_assessments")
    op.drop_table("triage_assessments")
