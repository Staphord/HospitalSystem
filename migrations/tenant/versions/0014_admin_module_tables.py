"""Administration module tables (FR-53–FR-58).

Adds departments, fee_schedules, insurance_providers, beds, audit_logs,
backup_jobs, role_permissions; extends users with admin columns.

Revision ID: 0014_admin_module_tables
Revises: 0013_make_timestamps_timezone_aware
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014_admin_module_tables"
down_revision: Union[str, None] = "0013_make_timestamps_timezone_aware"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "departments" not in tables:
        op.create_table(
            "departments",
            sa.Column("department_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("department_name", sa.String(100), nullable=False),
            sa.Column("department_type", sa.String(50), nullable=False),
            sa.Column("head_user_sub", sa.String(255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_departments_type", "departments", ["department_type"])

    if "fee_schedules" not in tables:
        op.create_table(
            "fee_schedules",
            sa.Column("fee_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("item_name", sa.String(200), nullable=False),
            sa.Column("item_code", sa.String(50), nullable=False),
            sa.Column("item_type", sa.String(50), nullable=False),
            sa.Column("standard_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("insurance_price", sa.Numeric(10, 2), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("item_code", name="uq_fee_schedules_item_code"),
        )
        op.create_index("idx_fee_schedules_type", "fee_schedules", ["item_type"])

    if "insurance_providers" not in tables:
        op.create_table(
            "insurance_providers",
            sa.Column("provider_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(150), nullable=False),
            sa.Column("contact_email", sa.String(150), nullable=True),
            sa.Column("contact_phone", sa.String(20), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("name", name="uq_insurance_providers_name"),
        )

    if "beds" not in tables:
        op.create_table(
            "beds",
            sa.Column("bed_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("ward_name", sa.String(100), nullable=False),
            sa.Column("bed_number", sa.String(20), nullable=False),
            sa.Column("bed_type", sa.String(50), nullable=False, server_default="general"),
            sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("ward_name", "bed_number", name="uq_beds_ward_number"),
        )
        op.create_index("idx_beds_ward", "beds", ["ward_name"])

    if "audit_logs" not in tables:
        op.create_table(
            "audit_logs",
            sa.Column("log_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("table_name", sa.String(100), nullable=False),
            sa.Column("record_id", sa.String(255), nullable=True),
            sa.Column("old_values", postgresql.JSONB(), nullable=True),
            sa.Column("new_values", postgresql.JSONB(), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("session_id", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_audit_logs_user", "audit_logs", ["user_id"])
        op.create_index("idx_audit_logs_action", "audit_logs", ["action"])
        op.create_index("idx_audit_logs_table", "audit_logs", ["table_name"])
        op.create_index("idx_audit_logs_created", "audit_logs", ["created_at"])

    if "backup_jobs" not in tables:
        op.create_table(
            "backup_jobs",
            sa.Column("backup_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("triggered_by", sa.String(32), nullable=False, server_default="user"),
            sa.Column("triggered_by_sub", sa.String(255), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_backup_jobs_tenant", "backup_jobs", ["tenant_id"])
        op.create_index("idx_backup_jobs_status", "backup_jobs", ["status"])

    if "role_permissions" not in tables:
        op.create_table(
            "role_permissions",
            sa.Column("permission_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("role_name", sa.String(50), nullable=False),
            sa.Column("modules", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("actions", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("role_name", name="uq_role_permissions_role"),
        )

    # Extend users
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "department_id" not in user_cols:
        op.add_column(
            "users",
            sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if "phone" not in user_cols:
        op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    if "last_login_at" not in user_cols:
        op.add_column(
            "users",
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "password_expires_at" not in user_cols:
        op.add_column(
            "users",
            sa.Column("password_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "mfa_enabled" not in user_cols:
        op.add_column(
            "users",
            sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"),
        )
    if "deleted_at" not in user_cols:
        op.add_column(
            "users",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    for col in (
        "deleted_at",
        "mfa_enabled",
        "password_expires_at",
        "last_login_at",
        "phone",
        "department_id",
    ):
        if col in user_cols:
            op.drop_column("users", col)

    for table in (
        "role_permissions",
        "backup_jobs",
        "audit_logs",
        "beds",
        "insurance_providers",
        "fee_schedules",
        "departments",
    ):
        if table in inspector.get_table_names():
            op.drop_table(table)
