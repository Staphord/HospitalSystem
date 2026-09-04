"""Add assistant conversation history tables.

Two tables in each tenant database, so a staff member can reopen what they
asked the hospital assistant before:

* assistant_conversations — one row per conversation thread, owned by the staff
  member who started it.
* assistant_messages — the questions and answers in that thread, in order.

Unlike the clinical decision support audit tables, these are deliberately NOT
append-only. History exists for the person who created it, so that person must
be able to delete a conversation, and deleting the thread must take its
messages with it. That is a user-facing store, not an audit trail: the audit
trail is unchanged and still records who asked, which capability, and which
content versions, never the words.

Rows live in the tenant database with the rest of that hospital's record and
are never copied into an application log. Both tables carry tenant_id and
actor_sub so every read can be scoped to the caller the token resolved to,
which is what stops one staff member reading another's history.

Revision ID: 0028_add_assistant_conversations
Revises: 0027_add_cds_differential_audit_tables
Create Date: 2026-08-29 09:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_add_assistant_conversations"
down_revision: Union[str, None] = "0027_add_cds_differential_audit_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if "assistant_conversations" not in existing:
        op.create_table(
            "assistant_conversations",
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            # The owner. Every read is scoped to this together with tenant_id.
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            # Derived on the server from the opening question, never sent by the
            # browser, so a title cannot be used to smuggle markup into the list.
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "last_message_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        # The list query is always "this owner's conversations, newest first".
        op.create_index(
            "ix_assistant_conversations_owner_recent",
            "assistant_conversations",
            ["tenant_id", "actor_sub", "last_message_at"],
        )

    if "assistant_messages" not in existing:
        op.create_table(
            "assistant_messages",
            sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "conversation_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("assistant_conversations.conversation_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("actor_sub", sa.String(length=255), nullable=False),
            # "user" or "assistant". Named author rather than role so it is never
            # confused with the caller's hospital role, which is not stored here.
            sa.Column("author", sa.String(length=16), nullable=False),
            # Position in the thread. A question and the answer to it are
            # written in the same transaction and so share a timestamp to the
            # microsecond; ordering on time alone would put them in an
            # arbitrary order when the thread is reopened.
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("body", sa.Text(), nullable=False),
            # Only set on an assistant message: supported, unsupported, or
            # unavailable, and the sources that answer cited.
            sa.Column("answer_status", sa.String(length=20), nullable=True),
            sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        # Reopening a thread reads its messages in the order they were written.
        op.create_index(
            "ix_assistant_messages_thread",
            "assistant_messages",
            ["conversation_id", "sequence"],
        )
        op.create_index(
            "ix_assistant_messages_owner",
            "assistant_messages",
            ["tenant_id", "actor_sub"],
        )


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
