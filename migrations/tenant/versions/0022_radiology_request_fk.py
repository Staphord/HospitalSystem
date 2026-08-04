"""Make radiology_reports.request_id required FK (1:1 with investigation_requests).

Revision ID: 0022_radiology_request_fk
Revises: 0021_admission_condition_and_vitals
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_radiology_request_fk"
down_revision: Union[str, None] = "0021_admission_condition_and_vitals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "radiology_reports" not in tables:
        return

    # Remove orphan reports so request_id can become NOT NULL
    op.execute("DELETE FROM radiology_reports WHERE request_id IS NULL")

    # Deduplicate if any request already has multiple reports — keep newest
    op.execute(
        """
        DELETE FROM radiology_reports
        WHERE report_id IN (
            SELECT report_id FROM (
                SELECT report_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY request_id
                           ORDER BY created_at DESC NULLS LAST, report_id DESC
                       ) AS rn
                FROM radiology_reports
                WHERE request_id IS NOT NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )

    op.alter_column(
        "radiology_reports",
        "request_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

    fks = {fk["name"] for fk in inspector.get_foreign_keys("radiology_reports")}
    if "fk_radiology_reports_request_id" not in fks and "investigation_requests" in tables:
        op.create_foreign_key(
            "fk_radiology_reports_request_id",
            "radiology_reports",
            "investigation_requests",
            ["request_id"],
            ["id"],
            ondelete="CASCADE",
        )

    uqs = {uq["name"] for uq in inspector.get_unique_constraints("radiology_reports")}
    indexes = {idx["name"] for idx in inspector.get_indexes("radiology_reports")}
    if "uq_radiology_reports_request_id" not in uqs and "uq_radiology_reports_request_id" not in indexes:
        op.create_unique_constraint(
            "uq_radiology_reports_request_id",
            "radiology_reports",
            ["request_id"],
        )

    if "idx_radiology_reports_request_id" not in indexes:
        op.create_index(
            "idx_radiology_reports_request_id",
            "radiology_reports",
            ["request_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "radiology_reports" not in inspector.get_table_names():
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("radiology_reports")}
    if "idx_radiology_reports_request_id" in indexes:
        op.drop_index("idx_radiology_reports_request_id", table_name="radiology_reports")

    uqs = {uq["name"] for uq in inspector.get_unique_constraints("radiology_reports")}
    if "uq_radiology_reports_request_id" in uqs:
        op.drop_constraint("uq_radiology_reports_request_id", "radiology_reports", type_="unique")

    fks = {fk["name"] for fk in inspector.get_foreign_keys("radiology_reports")}
    if "fk_radiology_reports_request_id" in fks:
        op.drop_constraint("fk_radiology_reports_request_id", "radiology_reports", type_="foreignkey")

    op.alter_column(
        "radiology_reports",
        "request_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
