"""extract agent runtime persistence from the product database

Revision ID: 20260727_0048
Revises: 20260723_0047
Create Date: 2026-07-27 00:00:00+00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260727_0048"
down_revision = "20260723_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove tables whose ownership moved to Agent Runtime."""

    op.drop_table("agent_tool_outputs")
    op.drop_table("agent_workflow_events")
    op.drop_table("user_fact_extraction_runs")
    op.drop_table("agent_memories")
    op.drop_table("agent_actions")
    op.drop_table("agent_artifacts")
    op.drop_table("agent_context_items")
    op.drop_table("agent_eval_cases")
    op.drop_table("agent_events")
    op.drop_table("agent_image_accesses")
    op.drop_table("agent_memory_consolidation_runs")
    op.drop_table("agent_memory_settings")
    op.drop_table("agent_memory_snapshots")
    op.drop_table("agent_model_context_snapshots")
    op.drop_table("agent_run_summaries")
    op.drop_table("legacy_agent_context_checkpoints")
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_messages")
    op.drop_table("agent_workflow_states")
    op.drop_table("user_facts")
    op.drop_table("agent_runs")
    op.drop_table("agent_threads")


def downgrade() -> None:
    raise RuntimeError(
        "Agent Runtime extraction is irreversible; restore a pre-migration "
        "database backup instead."
    )
