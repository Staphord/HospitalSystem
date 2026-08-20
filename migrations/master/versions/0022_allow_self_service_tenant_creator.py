"""Allow public tenant onboarding without an authenticated creator."""

from alembic import op


revision = "0022_allow_self_service_tenant_creator"
down_revision = "0021_add_keycloak_realm_to_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tenants", "created_by", nullable=True)


def downgrade() -> None:
    op.alter_column("tenants", "created_by", nullable=False)
