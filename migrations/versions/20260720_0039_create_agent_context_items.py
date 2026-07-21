"""create append-only context ledger and retire context projections

Revision ID: 20260720_0039
Revises: 20260720_0038
Create Date: 2026-07-20 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260720_0039"
down_revision = "20260720_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_context_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_key", sa.String(length=255), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("item_json", postgresql.JSONB(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_context_items_run_id_agent_runs",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_context_items_thread_id_agent_threads",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_agent_context_items_thread_sequence",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "item_key",
            name="uq_agent_context_items_thread_item_key",
        ),
    )
    op.create_index(
        "ix_agent_context_items_thread_sequence",
        "agent_context_items",
        ["thread_id", "sequence"],
    )
    op.create_index(
        "ix_agent_context_items_run_sequence",
        "agent_context_items",
        ["run_id", "sequence"],
    )
    op.drop_index("ix_agent_context_projections_workflow", table_name="agent_context_projections")
    op.drop_index("ix_agent_context_projections_thread_created", table_name="agent_context_projections")
    op.drop_index("ix_agent_context_projections_run_created", table_name="agent_context_projections")
    op.drop_table("agent_context_projections")


def downgrade() -> None:
    op.create_table(
        "agent_context_projections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("prompt_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("tool_schema_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column(
            "selected_message_ids_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("active_workflow_state_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "source_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "projection_summary_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_context_projections_run_id_agent_runs",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_context_projections_thread_id_agent_threads",
        ),
        sa.ForeignKeyConstraint(
            ["active_workflow_state_id"],
            ["agent_workflow_states.id"],
            name="fk_agent_context_proj_active_workflow_state",
        ),
    )
    op.create_index(
        "ix_agent_context_projections_run_created",
        "agent_context_projections",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_context_projections_thread_created",
        "agent_context_projections",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "ix_agent_context_projections_workflow",
        "agent_context_projections",
        ["active_workflow_state_id"],
    )
    op.drop_index("ix_agent_context_items_run_sequence", table_name="agent_context_items")
    op.drop_index("ix_agent_context_items_thread_sequence", table_name="agent_context_items")
    op.drop_table("agent_context_items")
