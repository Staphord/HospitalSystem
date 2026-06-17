"""Make announcements.created_by nullable.

Revision ID: 0004_announcement_creator_null
Revises: 0003_add_saas_schema
Create Date: 2026-06-15 00:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_announcement_creator_null"
down_revision: Union[str, None] = "0003_add_saas_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "announcements",
        "created_by",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "announcements",
        "created_by",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
