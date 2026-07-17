"""Add urgency to investigation_requests.

Revision ID: 0011_add_urgency_to_investigation_requests
Revises: 0010_create_laboratory_tables
Create Date: 2026-07-08 12:30:00.000000+03:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_add_urgency_to_investigation_requests"
down_revision: Union[str, None] = "0010_create_laboratory_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "investigation_requests",
        sa.Column("urgency", sa.String(length=50), nullable=False, server_default="routine")
    )


def downgrade() -> None:
    op.drop_column("investigation_requests", "urgency")
