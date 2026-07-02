"""create agent workflow state

Revision ID: 20260702_0016
Revises: 20260702_0015
Create Date: 2026-07-02 03:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0016"
down_revision = "20260702_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_workflow_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="collecting"),
        sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("state_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("active_step", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_workflow_states_thread_id_agent_threads"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_workflow_states_owner_user_id_users"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_workflow_states_run_id_agent_runs"),
    )
    op.create_index("ix_agent_workflow_states_thread_status", "agent_workflow_states", ["thread_id", "status"])
    op.create_index(
        "ix_agent_workflow_states_owner_type_status",
        "agent_workflow_states",
        ["owner_user_id", "workflow_type", "status"],
    )
    op.create_index("ix_agent_workflow_states_run_created", "agent_workflow_states", ["run_id", "created_at"])
    op.create_index("ix_agent_workflow_states_expires_at", "agent_workflow_states", ["expires_at"])

    op.create_table(
        "agent_context_projections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("prompt_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("tool_schema_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("selected_message_ids_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active_workflow_state_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_refs_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("projection_summary_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_context_projections_run_id_agent_runs"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_context_projections_thread_id_agent_threads"),
        sa.ForeignKeyConstraint(
            ["active_workflow_state_id"],
            ["agent_workflow_states.id"],
            name="fk_agent_context_proj_active_workflow_state",
        ),
    )
    op.create_index("ix_agent_context_projections_run_created", "agent_context_projections", ["run_id", "created_at"])
    op.create_index("ix_agent_context_projections_thread_created", "agent_context_projections", ["thread_id", "created_at"])
    op.create_index("ix_agent_context_projections_workflow", "agent_context_projections", ["active_workflow_state_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_context_projections_workflow", table_name="agent_context_projections")
    op.drop_index("ix_agent_context_projections_thread_created", table_name="agent_context_projections")
    op.drop_index("ix_agent_context_projections_run_created", table_name="agent_context_projections")
    op.drop_table("agent_context_projections")
    op.drop_index("ix_agent_workflow_states_expires_at", table_name="agent_workflow_states")
    op.drop_index("ix_agent_workflow_states_run_created", table_name="agent_workflow_states")
    op.drop_index("ix_agent_workflow_states_owner_type_status", table_name="agent_workflow_states")
    op.drop_index("ix_agent_workflow_states_thread_status", table_name="agent_workflow_states")
    op.drop_table("agent_workflow_states")
