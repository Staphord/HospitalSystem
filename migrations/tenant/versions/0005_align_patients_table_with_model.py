"""Align patients table with patient-service model.

The initial migration created a patients table with columns:
  patient_id, phone, emergency_contact, blood_type

But the patient-service model expects:
  id, phone_primary, next_of_kin_name, blood_group, etc.

This migration adds the new columns, migrates existing data,
and drops the old columns.

Revision ID: 0005_align_patients_table_with_model
Revises: 0004_add_user_mfa_fields
Create Date: 2026-06-26 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_align_patients_table_with_model"
down_revision: Union[str, None] = "0004_add_user_mfa_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if the table exists with the OLD schema (has patient_id column)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("patients")]

    # --- Add new columns (only if missing) ---
    if "id" not in columns:
        op.add_column("patients", sa.Column("id", postgresql.UUID(as_uuid=True), nullable=True))
        # Copy data from patient_id to id
        op.execute("UPDATE patients SET id = patient_id::uuid WHERE patient_id IS NOT NULL")

    if "phone_primary" not in columns:
        op.add_column("patients", sa.Column("phone_primary", sa.String(20), nullable=True))
        if "phone" in columns:
            op.execute("UPDATE patients SET phone_primary = phone WHERE phone IS NOT NULL")

    if "phone_secondary" not in columns:
        op.add_column("patients", sa.Column("phone_secondary", sa.String(20), nullable=True))

    if "next_of_kin_name" not in columns:
        op.add_column("patients", sa.Column("next_of_kin_name", sa.String(200), nullable=True))
        if "emergency_contact" in columns:
            op.execute("UPDATE patients SET next_of_kin_name = emergency_contact WHERE emergency_contact IS NOT NULL")

    if "next_of_kin_phone" not in columns:
        op.add_column("patients", sa.Column("next_of_kin_phone", sa.String(20), nullable=True))

    if "next_of_kin_relationship" not in columns:
        op.add_column("patients", sa.Column("next_of_kin_relationship", sa.String(50), nullable=True))

    if "blood_group" not in columns:
        op.add_column("patients", sa.Column("blood_group", sa.String(5), nullable=True))
        if "blood_type" in columns:
            op.execute("UPDATE patients SET blood_group = blood_type WHERE blood_type IS NOT NULL")

    if "national_id" not in columns:
        op.add_column("patients", sa.Column("national_id", sa.String(50), nullable=True))

    if "created_by" not in columns:
        op.add_column("patients", sa.Column("created_by", sa.String(36), nullable=True))

    # --- Drop old columns ---
    for old_col in ("phone", "emergency_contact", "blood_type"):
        if old_col in columns:
            op.drop_column("patients", old_col)

    # --- Set constraints ---
    # Generate UUIDs for any rows still missing id
    op.execute("""
        UPDATE patients
        SET id = gen_random_uuid()
        WHERE id IS NULL
    """)
    op.alter_column("patients", "id", nullable=False)
    op.create_primary_key("pk_patients", "patients", ["id"])

    # Set NOT NULL on phone_primary for any existing rows (fill empty string)
    if "phone_primary" in [c["name"] for c in inspector.get_columns("patients")]:
        op.execute("UPDATE patients SET phone_primary = '' WHERE phone_primary IS NULL")
        op.alter_column("patients", "phone_primary", nullable=False)

    # Create indexes for the new schema
    op.create_index("idx_patients_hospital_id", "patients", ["hospital_id"])
    op.create_index("idx_patients_patient_number", "patients", ["patient_number"])
    op.create_index("idx_patients_full_name", "patients", ["full_name"])
    op.create_unique_constraint("uq_hospital_patient_number", "patients", ["hospital_id", "patient_number"])
    op.create_unique_constraint("uq_hospital_national_id", "patients", ["hospital_id", "national_id"])


def downgrade() -> None:
    # This is a one-way migration - revert by restoring old columns if needed
    pass
