"""Add incidents table for system incident management.

Revision ID: 0007_add_incidents
Revises: 0006_add_keycloak_realm
Create Date: 2026-06-22 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0007_add_incidents"
down_revision: Union[str, None] = "0006_add_keycloak_realm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.tenant_id"), nullable=True),
        sa.Column("assigned_to", sa.String(64), sa.ForeignKey("super_admins.super_admin_id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(64), sa.ForeignKey("super_admins.super_admin_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_tenant_id", "incidents", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_tenant_id")
    op.drop_index("ix_incidents_status")
    op.drop_index("ix_incidents_severity")
    op.drop_table("incidents")
