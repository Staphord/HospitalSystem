"""Add global_roles and tenant_roles tables.

Revision ID: 0010_add_global_and_tenant_roles
Revises: 0009_align_tenant_columns_with_spec
Create Date: 2026-06-26 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_add_global_and_tenant_roles"
down_revision: Union[str, None] = "0009_align_tenant_columns_with_spec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create global_roles table (superadmin-created, available to all tenants)
    op.create_table(
        "global_roles",
        sa.Column("global_role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("super_admins.super_admin_id"),
            nullable=False,
        ),
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
    )

    # Create tenant_roles table (hospital-admin-created, scoped to tenant)
    op.create_table(
        "tenant_roles",
        sa.Column("tenant_role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(50), nullable=False, index=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
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
        sa.UniqueConstraint("name", "tenant_id", name="uq_tenant_role_name_per_tenant"),
    )


def downgrade() -> None:
    op.drop_table("tenant_roles")
    op.drop_table("global_roles")
