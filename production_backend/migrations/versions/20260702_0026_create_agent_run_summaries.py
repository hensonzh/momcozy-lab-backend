"""create agent run summaries

Revision ID: 20260702_0026
Revises: 20260702_0025
Create Date: 2026-07-08 20:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0026"
down_revision = "20260702_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_skill_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("summary_type", sa.String(length=40), nullable=False, server_default="run_fact"),
        sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_message_ids_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_tool_call_ids_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_run_summaries_owner_user_id_users"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_run_summaries_run_id_agent_runs"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_run_summaries_thread_id_agent_threads"),
        sa.UniqueConstraint("run_id", "summary_type", name="uq_agent_run_summaries_run_type"),
    )
    op.create_index("ix_agent_run_summaries_thread_created", "agent_run_summaries", ["thread_id", "created_at"])
    op.create_index(
        "ix_agent_run_summaries_owner_skill_created",
        "agent_run_summaries",
        ["owner_user_id", "service_skill_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_summaries_owner_skill_created", table_name="agent_run_summaries")
    op.drop_index("ix_agent_run_summaries_thread_created", table_name="agent_run_summaries")
    op.drop_table("agent_run_summaries")
