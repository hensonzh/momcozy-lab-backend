"""drop unused agent routing persistence

Revision ID: 20260720_0037
Revises: 20260716_0036
Create Date: 2026-07-20 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260720_0037"
down_revision = "20260716_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("agent_routing_decisions")
    op.drop_column("agent_runs", "routing_summary_json")
    op.drop_column("agent_runs", "routing_confidence_score")
    op.drop_column("agent_runs", "routing_source")


def downgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("routing_source", sa.String(length=80), nullable=False, server_default=""),
    )
    op.add_column(
        "agent_runs",
        sa.Column("routing_confidence_score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "routing_summary_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "agent_routing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_skill_id", sa.String(length=80), nullable=False),
        sa.Column("routing_source", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_mode", sa.String(length=40), nullable=False, server_default="passthrough"),
        sa.Column("intents_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reason_codes_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("safety_flags_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("needs_clarification", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tool_scope_version", sa.String(length=80), nullable=False, server_default="default"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_agent_routing_decisions_actor_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_messages.id"],
            name="fk_agent_routing_decisions_message_id_agent_messages",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_routing_decisions_run_id_agent_runs",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_routing_decisions_thread_id_agent_threads",
        ),
        sa.UniqueConstraint(
            "run_id",
            "message_id",
            name="uq_agent_routing_decisions_run_message",
        ),
    )
    op.create_index(
        "ix_agent_routing_decisions_run_created",
        "agent_routing_decisions",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_routing_decisions_thread_created",
        "agent_routing_decisions",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "ix_agent_routing_decisions_service_skill_created",
        "agent_routing_decisions",
        ["selected_skill_id", "created_at"],
    )
