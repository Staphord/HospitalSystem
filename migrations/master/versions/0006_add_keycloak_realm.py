"""Add keycloak_realm to tenants.

Revision ID: 0006_add_keycloak_realm
Revises: 0005_add_termination_columns
Create Date: 2026-06-16 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_add_keycloak_realm"
down_revision: Union[str, None] = "0005_add_termination_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("keycloak_realm", sa.String(255), nullable=True, server_default="hospital-realm"))
    op.execute("UPDATE tenants SET keycloak_realm = 'hospital-realm' WHERE keycloak_realm IS NULL")
    op.alter_column("tenants", "keycloak_realm", server_default=None)


def downgrade() -> None:
    op.drop_column("tenants", "keycloak_realm")
