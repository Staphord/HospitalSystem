"""Hospital settings key-value store for admin configuration extras.

Revision ID: 0017_hospital_settings
Revises: 0016_ward_module_tables
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_hospital_settings"
down_revision: Union[str, None] = "0016_ward_module_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "hospital_settings" not in tables:
        op.create_table(
            "hospital_settings",
            sa.Column("key", sa.String(length=100), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_by", sa.String(length=255), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "hospital_settings" in set(inspector.get_table_names()):
        op.drop_table("hospital_settings")
