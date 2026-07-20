"""Ward visitor logs and shift handovers.

Revision ID: 0018_ward_visitors_handover
Revises: 0017_hospital_settings
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0018_ward_visitors_handover"
down_revision: Union[str, None] = "0017_hospital_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "visitor_logs" not in tables:
        op.create_table(
            "visitor_logs",
            sa.Column("visitor_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("admission_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("patient_name", sa.String(200), nullable=False),
            sa.Column("bed_label", sa.String(50), nullable=False),
            sa.Column("visitor_name", sa.String(200), nullable=False),
            sa.Column("relationship", sa.String(100), nullable=False),
            sa.Column("national_id", sa.String(100), nullable=True),
            sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(255), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("denial_reason", sa.Text(), nullable=True),
            sa.Column("allowed_duration_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("ward_name", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_visitor_logs_status", "visitor_logs", ["status"])
        op.create_index("idx_visitor_logs_admission", "visitor_logs", ["admission_id"])

    if "shift_handovers" not in tables:
        op.create_table(
            "shift_handovers",
            sa.Column("handover_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("shift_label", sa.String(50), nullable=False),
            sa.Column("submitted_by", sa.String(255), nullable=False),
            sa.Column("overall_summary", sa.Text(), nullable=False),
            sa.Column("incidents_summary", sa.String(100), nullable=True),
            sa.Column("patient_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("patient_notes", postgresql.JSONB(), nullable=True),
            sa.Column("ward_name", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("idx_shift_handovers_created", "shift_handovers", ["created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "shift_handovers" in tables:
        op.drop_table("shift_handovers")
    if "visitor_logs" in tables:
        op.drop_table("visitor_logs")
