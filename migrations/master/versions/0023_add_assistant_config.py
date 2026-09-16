"""Add the platform assistant configuration row.

The Hospital Assistant's model provider and request bounds used to be
environment variables on report-service. They move here so a platform super
admin can change a model, rotate a key, or tighten a limit from the portal
without a deploy.

One row, id 1, in the master database: this configures the platform's own model
provider, not a hospital's data, so it is deliberately not tenant-scoped.

The row is seeded with the same defaults the code carries, so a deployment that
applies this migration and opens the portal sees the values it was already
running with rather than an empty form. The API key column is left null on
purpose - it is set from the portal, encrypted, and is never seeded from a
migration.

Revision ID: 0023_add_assistant_config
Revises: 0022_allow_self_service_tenant_creator
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_add_assistant_config"
down_revision = "0022_allow_self_service_tenant_creator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="groq"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "base_url",
            sa.String(length=255),
            nullable=False,
            server_default="https://api.groq.com/openai/v1",
        ),
        sa.Column(
            "model",
            sa.String(length=128),
            nullable=False,
            server_default="openai/gpt-oss-120b",
        ),
        sa.Column(
            "transcription_model",
            sa.String(length=128),
            nullable=False,
            server_default="whisper-large-v3",
        ),
        sa.Column("max_question_chars", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column(
            "request_timeout_seconds", sa.Integer(), nullable=False, server_default="20"
        ),
        sa.Column(
            "history_max_conversations", sa.Integer(), nullable=False, server_default="50"
        ),
        sa.Column(
            "history_max_messages", sa.Integer(), nullable=False, server_default="200"
        ),
        sa.Column(
            "max_audio_bytes", sa.Integer(), nullable=False, server_default="5242880"
        ),
        sa.Column(
            "max_audio_duration_ms", sa.Integer(), nullable=False, server_default="60000"
        ),
        sa.Column(
            "voice_timeout_seconds", sa.Integer(), nullable=False, server_default="20"
        ),
        sa.Column(
            "live_data_cache_seconds", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column(
            "live_data_timeout_seconds", sa.Integer(), nullable=False, server_default="3"
        ),
        sa.Column(
            "live_data_max_metrics", sa.Integer(), nullable=False, server_default="3"
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Seed the single row. Every column carries a server default, so this needs
    # to name only the primary key.
    op.execute("INSERT INTO assistant_config (id) VALUES (1)")


def downgrade() -> None:
    # Dropping this returns the service to its built-in defaults, plus whatever
    # GROQ_* bootstrap values the environment still carries. A stored API key
    # is destroyed with the table and has to be set again.
    op.drop_table("assistant_config")
