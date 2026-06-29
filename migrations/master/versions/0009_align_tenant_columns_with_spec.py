"""Align tenant columns with spec: rename name→hospital_name,
db_dsn_encrypted→db_connection_string, make created_by NOT NULL.

Revision ID: 0009_align_tenant_columns_with_spec
Revises: 0008_add_pending_plan
Create Date: 2026-06-25 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_align_tenant_columns_with_spec"
down_revision: Union[str, None] = "0008_add_pending_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Rename name → hospital_name
    # ------------------------------------------------------------------
    op.add_column("tenants", sa.Column("hospital_name", sa.String(length=200), nullable=True))
    op.execute("UPDATE tenants SET hospital_name = name")
    op.alter_column("tenants", "hospital_name", nullable=False)
    op.drop_column("tenants", "name")

    # ------------------------------------------------------------------
    # 2. Rename db_dsn_encrypted → db_connection_string
    # ------------------------------------------------------------------
    op.add_column("tenants", sa.Column("db_connection_string", sa.Text(), nullable=True))
    op.execute("UPDATE tenants SET db_connection_string = db_dsn_encrypted")
    op.alter_column("tenants", "db_connection_string", nullable=False)
    op.drop_column("tenants", "db_dsn_encrypted")

    # ------------------------------------------------------------------
    # 3. Make created_by NOT NULL
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE tenants SET created_by = '00000000-0000-0000-0000-000000000000' "
        "WHERE created_by IS NULL"
    )
    op.alter_column("tenants", "created_by", nullable=False)


def downgrade() -> None:
    # Reverse created_by
    op.alter_column("tenants", "created_by", nullable=True)

    # Reverse db_connection_string → db_dsn_encrypted
    op.add_column("tenants", sa.Column("db_dsn_encrypted", sa.Text(), nullable=True))
    op.execute("UPDATE tenants SET db_dsn_encrypted = db_connection_string")
    op.alter_column("tenants", "db_dsn_encrypted", nullable=False)
    op.drop_column("tenants", "db_connection_string")

    # Reverse hospital_name → name
    op.add_column("tenants", sa.Column("name", sa.String(length=255), nullable=True))
    op.execute("UPDATE tenants SET name = hospital_name")
    op.alter_column("tenants", "name", nullable=False)
    op.drop_column("tenants", "hospital_name")
