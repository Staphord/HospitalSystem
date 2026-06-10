"""Initial tenant schema migration.

Revision ID: 0001_initial_tenant_schema
Revises: 
Create Date: 2024-01-01 00:00:00.000000+00:00

Tenant databases contain all clinical tables (Patient, Visit, TriageAssessment,
Consultation, etc.). Each service manages its own domain tables via subsequent
migrations. This initial revision creates a placeholder to establish the baseline.

Common tables that are master-only (users, refresh_tokens, password_reset_tokens,
super_admins, tenants, global_audit_logs) are NOT created here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_tenant_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Placeholder: actual clinical tables are added by service-specific migrations.
    # Example: Patient, Visit, TriageAssessment, Consultation, LabResult, etc.
    pass


def downgrade() -> None:
    pass
