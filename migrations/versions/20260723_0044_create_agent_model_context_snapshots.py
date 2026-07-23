"""create replayable bounded model-context snapshots

Revision ID: 20260723_0044
Revises: 20260723_0043
Create Date: 2026-07-23 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260723_0044"
down_revision = "20260723_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_model_context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=80),
            nullable=False,
            server_default="model_context_snapshot.v1",
        ),
        sa.Column(
            "item_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "dynamic_context_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "selection_policy_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("input_item_count", sa.Integer(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("model_input_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_model_context_snapshots_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_model_context_snapshots_thread_id",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_agent_model_context_snapshots_owner_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_model_context_snapshots_run_sequence",
        ),
    )
    op.create_index(
        "ix_agent_model_context_snapshots_run_sequence",
        "agent_model_context_snapshots",
        ["run_id", "sequence"],
    )
    op.create_index(
        "ix_agent_model_context_snapshots_owner_created",
        "agent_model_context_snapshots",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_model_context_snapshots_owner_created",
        table_name="agent_model_context_snapshots",
    )
    op.drop_index(
        "ix_agent_model_context_snapshots_run_sequence",
        table_name="agent_model_context_snapshots",
    )
    op.drop_table("agent_model_context_snapshots")
