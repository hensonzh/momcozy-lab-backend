"""create agent memories

Revision ID: 20260702_0018
Revises: 20260702_0017
Create Date: 2026-07-02 12:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0018"
down_revision = "20260702_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("content_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_memories_owner_user_id_users"),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], name="fk_agent_memories_source_run_id_agent_runs"),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["agent_messages.id"],
            name="fk_agent_memories_source_message_id_agent_messages",
        ),
    )
    op.create_index(
        "ix_agent_memories_owner_type_status",
        "agent_memories",
        ["owner_user_id", "memory_type", "status"],
    )
    op.create_index("ix_agent_memories_owner_updated", "agent_memories", ["owner_user_id", "updated_at"])
    op.create_index("ix_agent_memories_source_run", "agent_memories", ["source_run_id"])
    op.create_index("ix_agent_memories_expires_at", "agent_memories", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_expires_at", table_name="agent_memories")
    op.drop_index("ix_agent_memories_source_run", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_updated", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_type_status", table_name="agent_memories")
    op.drop_table("agent_memories")
