"""Alter visits patient_id to UUID.

Revision ID: 0008_alter_visits_patient_id_to_uuid
Revises: 0007_create_consultations
Create Date: 2026-06-29 16:56:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_alter_visits_patient_id_to_uuid"
down_revision: Union[str, None] = "0007_create_consultations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_uuid_type(col) -> bool:
    type_name = type(col["type"]).__name__.lower()
    raw = str(col["type"]).lower()
    return "uuid" in type_name or raw.startswith("uuid")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "visits" not in inspector.get_table_names():
        return

    visit_cols = {c["name"]: c for c in inspector.get_columns("visits")}
    patient_col = visit_cols.get("patient_id")
    if patient_col is None or _is_uuid_type(patient_col):
        return

    # Only convert when the referenced patients key is already UUID-typed.
    if "patients" not in inspector.get_table_names():
        return
    patient_cols = {c["name"]: c for c in inspector.get_columns("patients")}
    patients_id = patient_cols.get("id")
    patients_patient_id = patient_cols.get("patient_id")
    target_is_uuid = (patients_id is not None and _is_uuid_type(patients_id)) or (
        patients_patient_id is not None and _is_uuid_type(patients_patient_id)
    )
    if not target_is_uuid:
        # Legacy schema: patients.patient_id is varchar — keep visits.patient_id as varchar.
        return

    result = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FILTER (
                WHERE patient_id IS NOT NULL
                  AND patient_id !~*
                      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            ) AS bad_count
            FROM visits
            """
        )
    ).scalar()
    if result and int(result) > 0:
        return

    # Drop FK temporarily if present, alter, recreate.
    fks = inspector.get_foreign_keys("visits")
    dropped = []
    for fk in fks:
        if fk.get("constrained_columns") == ["patient_id"]:
            name = fk.get("name")
            if name:
                op.drop_constraint(name, "visits", type_="foreignkey")
                dropped.append(fk)

    op.execute(
        "ALTER TABLE visits ALTER COLUMN patient_id TYPE uuid USING patient_id::uuid"
    )

    for fk in dropped:
        referred = fk.get("referred_table")
        referred_cols = fk.get("referred_columns") or ["id"]
        name = fk.get("name")
        op.create_foreign_key(
            name,
            "visits",
            referred,
            ["patient_id"],
            referred_cols,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "visits" not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns("visits")}
    patient_col = cols.get("patient_id")
    if patient_col is None or not _is_uuid_type(patient_col):
        return
    op.execute("ALTER TABLE visits ALTER COLUMN patient_id TYPE varchar(36)")
