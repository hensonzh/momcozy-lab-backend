"""create agent runtime ledger

Revision ID: 20260702_0012
Revises: 20260702_0011
Create Date: 2026-07-02 02:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0012"
down_revision = "20260702_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_threads_owner_user_id_users"),
    )
    op.create_index("ix_agent_threads_owner_updated", "agent_threads", ["owner_user_id", "updated_at"])
    op.create_index("ix_agent_threads_owner_status_updated", "agent_threads", ["owner_user_id", "status", "updated_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("runtime_pattern", sa.String(length=64), nullable=False, server_default="langgraph_sdk"),
        sa.Column("graph_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("request_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("error_details_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_agent_runs_actor_user_id_users"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_runs_thread_id_agent_threads"),
    )
    op.create_index("ix_agent_runs_thread_started", "agent_runs", ["thread_id", "started_at"])
    op.create_index("ix_agent_runs_actor_status_started", "agent_runs", ["actor_user_id", "status", "started_at"])
    op.create_index("ix_agent_runs_request_id", "agent_runs", ["request_id"])
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("content_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_messages_run_id_agent_runs"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_messages_thread_id_agent_threads"),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_agent_messages_thread_sequence"),
    )
    op.create_index("ix_agent_messages_thread_sequence", "agent_messages", ["thread_id", "sequence"])
    op.create_index("ix_agent_messages_run_created", "agent_messages", ["run_id", "created_at"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("call_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="started"),
        sa.Column("safe_args_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_tool_calls_run_id_agent_runs"),
        sa.UniqueConstraint("run_id", "call_id", name="uq_agent_tool_calls_run_call_id"),
    )
    op.create_index("ix_agent_tool_calls_run_status", "agent_tool_calls", ["run_id", "status"])
    op.create_index("ix_agent_tool_calls_run_tool", "agent_tool_calls", ["run_id", "tool_name"])

    op.create_table(
        "agent_tool_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("safe_output_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_output_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tool_call_id"], ["agent_tool_calls.id"], name="fk_agent_tool_outputs_tool_call_id_agent_tool_calls"),
    )
    op.create_index("ix_agent_tool_outputs_tool_call", "agent_tool_outputs", ["tool_call_id"])

    op.create_table(
        "agent_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_events_run_id_agent_runs"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_events_thread_id_agent_threads"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_sequence"),
    )
    op.create_index("ix_agent_events_run_sequence", "agent_events", ["run_id", "sequence"])
    op.create_index("ix_agent_events_thread_created", "agent_events", ["thread_id", "created_at"])
    op.create_index("ix_agent_events_type_created", "agent_events", ["event_type", "created_at"])

    op.create_table(
        "agent_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_payload_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_artifacts_owner_user_id_users"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_artifacts_run_id_agent_runs"),
    )
    op.create_index("ix_agent_artifacts_run_status", "agent_artifacts", ["run_id", "status"])
    op.create_index("ix_agent_artifacts_owner_type_created", "agent_artifacts", ["owner_user_id", "artifact_type", "created_at"])

    op.create_table(
        "agent_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("target_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("side_effect_level", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("preview_payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("apply_payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_agent_actions_actor_user_id_users"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_actions_run_id_agent_runs"),
    )
    op.create_index("ix_agent_actions_run_status", "agent_actions", ["run_id", "status"])
    op.create_index("ix_agent_actions_actor_status", "agent_actions", ["actor_user_id", "status"])
    op.create_index("ix_agent_actions_idempotency_key", "agent_actions", ["idempotency_key"])

    op.create_table(
        "agent_context_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("checkpoint_namespace", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=120), nullable=False),
        sa.Column("graph_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("state_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("state_summary_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_context_checkpoints_run_id_agent_runs"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], name="fk_agent_context_checkpoints_thread_id_agent_threads"),
        sa.UniqueConstraint("checkpoint_namespace", "checkpoint_id", name="uq_agent_context_checkpoints_namespace_id"),
    )
    op.create_index(
        "ix_agent_context_checkpoints_thread_created",
        "agent_context_checkpoints",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "ix_agent_context_checkpoints_run_created",
        "agent_context_checkpoints",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_context_checkpoints_run_created", table_name="agent_context_checkpoints")
    op.drop_index("ix_agent_context_checkpoints_thread_created", table_name="agent_context_checkpoints")
    op.drop_table("agent_context_checkpoints")
    op.drop_index("ix_agent_actions_idempotency_key", table_name="agent_actions")
    op.drop_index("ix_agent_actions_actor_status", table_name="agent_actions")
    op.drop_index("ix_agent_actions_run_status", table_name="agent_actions")
    op.drop_table("agent_actions")
    op.drop_index("ix_agent_artifacts_owner_type_created", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_run_status", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index("ix_agent_events_type_created", table_name="agent_events")
    op.drop_index("ix_agent_events_thread_created", table_name="agent_events")
    op.drop_index("ix_agent_events_run_sequence", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index("ix_agent_tool_outputs_tool_call", table_name="agent_tool_outputs")
    op.drop_table("agent_tool_outputs")
    op.drop_index("ix_agent_tool_calls_run_tool", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_run_status", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_messages_run_created", table_name="agent_messages")
    op.drop_index("ix_agent_messages_thread_sequence", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_runs_trace_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_request_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_actor_status_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_thread_started", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_threads_owner_status_updated", table_name="agent_threads")
    op.drop_index("ix_agent_threads_owner_updated", table_name="agent_threads")
    op.drop_table("agent_threads")
