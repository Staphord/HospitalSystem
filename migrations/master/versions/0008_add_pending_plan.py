"""Add pending_plan and pending_billing_cycle columns for deferred downgrade.

Revision ID: 0008_add_pending_plan
Revises: 0007_add_incidents
Create Date: 2026-06-22 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_add_pending_plan"
down_revision: Union[str, None] = "0007_add_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("pending_plan", sa.String(64), nullable=True))
    op.add_column("tenants", sa.Column("pending_billing_cycle", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "pending_billing_cycle")
    op.drop_column("tenants", "pending_plan")
