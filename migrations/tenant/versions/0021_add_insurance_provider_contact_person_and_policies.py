"""Add contact_person and policies to insurance_providers table

Revision ID: 0021_add_insurance_provider_contact_person_and_policies
Revises: 0020_merge_tenant_heads
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0021_add_insurance_provider_contact_person_and_policies"
down_revision: Union[str, None] = "0020_merge_tenant_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "insurance_providers" in tables:
        cols = {c["name"] for c in inspector.get_columns("insurance_providers")}
        if "contact_person" not in cols:
            op.add_column(
                "insurance_providers",
                sa.Column("contact_person", sa.String(150), nullable=True),
            )
        if "policies" not in cols:
            op.add_column(
                "insurance_providers",
                sa.Column(
                    "policies",
                    postgresql.JSONB(),
                    nullable=False,
                    server_default="[]",
                ),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "insurance_providers" in tables:
        cols = {c["name"] for c in inspector.get_columns("insurance_providers")}
        if "policies" in cols:
            op.drop_column("insurance_providers", "policies")
        if "contact_person" in cols:
            op.drop_column("insurance_providers", "contact_person")
