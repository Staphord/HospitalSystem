"""Increase invoice_number column length to 64.

Revision ID: 0013_increase_invoice_number_length
Revises: 0012_merge_heads
Create Date: 2026-07-10 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers
revision: str = "0013_increase_invoice_number_length"
down_revision: Union[str, Sequence[str], None] = "0012_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "invoices",
        "invoice_number",
        type_=sa.String(64),
        existing_type=sa.String(30),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "invoices",
        "invoice_number",
        type_=sa.String(30),
        existing_type=sa.String(64),
        existing_nullable=False,
    )
