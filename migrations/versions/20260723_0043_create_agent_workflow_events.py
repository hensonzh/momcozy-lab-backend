"""create append-only workflow event ledger and pregnancy resume invariant

This test-stage cutover intentionally requires a fresh pregnancy workflow
state set. It does not backfill, revive, merge, or delete legacy states.

Revision ID: 20260723_0043
Revises: 20260722_0042
Create Date: 2026-07-23 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260723_0043"
down_revision = "20260722_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $migration$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM agent_workflow_states
            WHERE workflow_type = 'pregnancy_plan'
          ) THEN
            RAISE EXCEPTION
              'pregnancy workflow migration requires an empty pregnancy-plan workflow state set; reset the disposable test database before upgrade';
          END IF;
        END
        $migration$;
        """
    )
    op.create_index(
        "uq_agent_workflow_states_owner_type_active",
        "agent_workflow_states",
        ["owner_user_id", "workflow_type"],
        unique=True,
        postgresql_where=sa.text(
            "workflow_type = 'pregnancy_plan' "
            "AND status IN ('collecting', 'ready', 'waiting', 'paused')"
        ),
    )
    op.create_table(
        "agent_workflow_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_type", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_state_id"],
            ["agent_workflow_states.id"],
            name="fk_agent_workflow_events_state_id",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_agent_workflow_events_owner_id",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_workflow_events_thread_id",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_workflow_events_run_id",
        ),
        sa.UniqueConstraint(
            "workflow_state_id",
            "sequence",
            name="uq_agent_workflow_events_state_sequence",
        ),
    )
    op.create_index(
        "ix_agent_workflow_events_state_sequence",
        "agent_workflow_events",
        ["workflow_state_id", "sequence"],
    )
    op.create_index(
        "ix_agent_workflow_events_owner_type_created",
        "agent_workflow_events",
        ["owner_user_id", "workflow_type", "created_at"],
    )
    op.create_index(
        "ix_agent_workflow_events_run_created",
        "agent_workflow_events",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_workflow_events_run_created", table_name="agent_workflow_events")
    op.drop_index("ix_agent_workflow_events_owner_type_created", table_name="agent_workflow_events")
    op.drop_index("ix_agent_workflow_events_state_sequence", table_name="agent_workflow_events")
    op.drop_table("agent_workflow_events")
    op.drop_index("uq_agent_workflow_states_owner_type_active", table_name="agent_workflow_states")
