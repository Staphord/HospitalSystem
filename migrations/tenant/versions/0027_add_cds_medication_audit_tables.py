"""Add append-only clinical decision support audit tables.

Two tables in each tenant database:

* cds_medication_checks — one row per medication check that ran: who ran it,
  acting in which clinical role, against which visit, what the check concluded,
  and which version of which approved ruleset answered.
* cds_alert_actions — one row per acknowledgement or override, with the reason
  an override required.

Both are append-only, enforced in the database rather than only in application
code: a trigger refuses UPDATE and DELETE on either table. A record of what a
clinician was shown, and what they decided about it, must not be rewritable
afterwards by any service, script, or person with a database connection.

Neither table stores patient names, drug names, or finding text. A finding is
recorded by its content-derived finding_id together with the ruleset version
that produced it, which is enough to reproduce exactly what was displayed.

Revision ID: 0027_add_cds_medication_audit_tables
Revises: 0026_add_consultation_disposition_columns
Create Date: 2026-08-27 19:30:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_add_cds_medication_audit_tables"
down_revision: Union[str, None] = "0026_add_consultation_disposition_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION cds_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'clinical decision support audit rows are append-only';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if "cds_medication_checks" not in existing:
        op.create_table(
            "cds_medication_checks",
            sa.Column("check_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            sa.Column("actor_role", sa.String(length=64), nullable=False),
            sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("needs_review_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "finding_index",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("ruleset_source", sa.String(length=120), nullable=True),
            sa.Column("ruleset_version", sa.String(length=64), nullable=True),
            sa.Column("ruleset_effective_date", sa.Date(), nullable=True),
            sa.Column("ruleset_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_cds_medication_checks_request_id", "cds_medication_checks", ["request_id"]
        )
        op.create_index(
            "ix_cds_medication_checks_tenant_id", "cds_medication_checks", ["tenant_id"]
        )
        op.create_index(
            "ix_cds_medication_checks_actor_sub", "cds_medication_checks", ["actor_sub"]
        )
        op.create_index(
            "ix_cds_medication_checks_visit_id", "cds_medication_checks", ["visit_id"]
        )
        op.create_index(
            "ix_cds_medication_checks_patient_id", "cds_medication_checks", ["patient_id"]
        )

    if "cds_alert_actions" not in existing:
        op.create_table(
            "cds_alert_actions",
            sa.Column("action_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "check_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("cds_medication_checks.check_id"),
                nullable=False,
            ),
            sa.Column("finding_id", sa.String(length=64), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            sa.Column("actor_role", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("ix_cds_alert_actions_check_id", "cds_alert_actions", ["check_id"])
        op.create_index("ix_cds_alert_actions_finding_id", "cds_alert_actions", ["finding_id"])
        op.create_index("ix_cds_alert_actions_tenant_id", "cds_alert_actions", ["tenant_id"])
        op.create_index("ix_cds_alert_actions_actor_sub", "cds_alert_actions", ["actor_sub"])
        op.create_index("ix_cds_alert_actions_request_id", "cds_alert_actions", ["request_id"])

    op.execute(APPEND_ONLY_FUNCTION)
    for table in ("cds_medication_checks", "cds_alert_actions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION cds_append_only()"
        )


def downgrade() -> None:
    for table in ("cds_alert_actions", "cds_medication_checks"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS cds_append_only()")
    op.drop_table("cds_alert_actions")
    op.drop_table("cds_medication_checks")
