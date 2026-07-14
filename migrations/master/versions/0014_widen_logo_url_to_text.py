"""Widen logo_url column from VARCHAR(255) to Text.

Revision ID: 0014_widen_logo_url_to_text
Revises: 0013_increase_invoice_number_length
Create Date: 2026-07-13 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers
revision: str = "0014_widen_logo_url_to_text"
down_revision: Union[str, Sequence[str], None] = "0013_increase_invoice_number_length"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "logo_url",
        type_=sa.Text(),
        existing_type=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "logo_url",
        type_=sa.String(255),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
