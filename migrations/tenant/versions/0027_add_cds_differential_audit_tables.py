"""Add append-only clinical differential support audit tables.

Two tables in each tenant database:

* cds_differential_suggestions — one row per differential suggestion issued:
  who asked, acting in which clinical role, against which visit and department,
  what the result concluded, which deterministic red-flag rules fired, and the
  knowledge, rule-pack, prompt, and model versions that produced it.
* cds_differential_feedback — one row per clinician judgement of a suggestion.

Both are append-only, enforced in the database rather than only in application
code: a trigger refuses UPDATE and DELETE on either table. A record of what a
clinician was shown, and what they thought of it, must not be rewritable
afterwards by any service, script, or person with a database connection.

Neither table stores the considerations, the rationale, the chief complaint, or
any other clinical narrative. A suggestion is recorded by its id together with
the versions that produced it and the rule ids that fired, which is what an
auditor needs to trace it without keeping a second copy of the clinical text.
The feedback comment is the one exception and stays in the tenant database with
the rest of that hospital's record, never in a log.

Revision ID: 0027_add_cds_differential_audit_tables
Revises: 0026_add_consultation_disposition_columns
Create Date: 2026-08-28 10:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_add_cds_differential_audit_tables"
down_revision: Union[str, None] = "0026_add_consultation_disposition_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# This migration owns the append-only trigger function outright.
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION cds_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'clinical decision support audit rows are append-only';
END;
$$ LANGUAGE plpgsql;
"""

TABLES = ("cds_differential_suggestions", "cds_differential_feedback")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if "cds_differential_suggestions" not in existing:
        op.create_table(
            "cds_differential_suggestions",
            sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            sa.Column("actor_role", sa.String(length=64), nullable=False),
            sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("department", sa.String(length=60), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("consideration_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("red_flag_count", sa.Integer(), nullable=False, server_default="0"),
            # Which deterministic rules fired, by id. Identifiers only.
            sa.Column("red_flag_rule_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("knowledge_version", sa.String(length=64), nullable=False),
            sa.Column("redflag_ruleset_version", sa.String(length=64), nullable=False),
            sa.Column("prompt_version", sa.String(length=64), nullable=False),
            sa.Column("model_version", sa.String(length=120), nullable=True),
            sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        for column in ("request_id", "tenant_id", "actor_sub", "visit_id", "patient_id"):
            op.create_index(
                f"ix_cds_differential_suggestions_{column}",
                "cds_differential_suggestions",
                [column],
            )

    if "cds_differential_feedback" not in existing:
        op.create_table(
            "cds_differential_feedback",
            sa.Column("feedback_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "suggestion_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("cds_differential_suggestions.suggestion_id"),
                nullable=False,
            ),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            sa.Column("actor_role", sa.String(length=64), nullable=False),
            sa.Column("rating", sa.String(length=20), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        for column in ("suggestion_id", "tenant_id", "actor_sub", "request_id"):
            op.create_index(
                f"ix_cds_differential_feedback_{column}",
                "cds_differential_feedback",
                [column],
            )

    op.execute(APPEND_ONLY_FUNCTION)
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION cds_append_only()"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_table("cds_differential_feedback")
    op.drop_table("cds_differential_suggestions")
    op.execute("DROP FUNCTION IF EXISTS cds_append_only()")
