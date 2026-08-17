"""Merge tenant migration heads.

Revision ID: 0023_merge_tenant_heads
Revises: 0021_add_insurance_provider_contact_person_and_policies, 0022_radiology_request_fk
Create Date: 2026-08-13 00:00:00.000000+00:00
"""

from typing import Sequence, Union

revision: str = "0023_merge_tenant_heads"
down_revision: Union[tuple[str, ...], None] = (
    "0021_add_insurance_provider_contact_person_and_policies",
    "0022_radiology_request_fk",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
