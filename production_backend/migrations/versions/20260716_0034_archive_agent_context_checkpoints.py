"""archive retired agent context checkpoints

Revision ID: 20260716_0034
Revises: 20260713_0033
Create Date: 2026-07-16 10:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260716_0034"
down_revision = "20260713_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("agent_context_checkpoints", "legacy_agent_context_checkpoints")
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT pk_agent_context_checkpoints TO pk_legacy_agent_context_checkpoints"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT uq_agent_context_checkpoints_namespace_id "
        "TO uq_legacy_agent_context_checkpoints_namespace_id"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT fk_agent_context_checkpoints_thread_id_agent_threads "
        "TO fk_legacy_agent_context_checkpoints_thread_id_agent_threads"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT fk_agent_context_checkpoints_run_id_agent_runs "
        "TO fk_legacy_agent_context_checkpoints_run_id_agent_runs"
    )
    op.execute(
        "ALTER INDEX ix_agent_context_checkpoints_thread_created "
        "RENAME TO ix_legacy_agent_context_checkpoints_thread_created"
    )
    op.execute(
        "ALTER INDEX ix_agent_context_checkpoints_run_created "
        "RENAME TO ix_legacy_agent_context_checkpoints_run_created"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_legacy_agent_context_checkpoints_run_created "
        "RENAME TO ix_agent_context_checkpoints_run_created"
    )
    op.execute(
        "ALTER INDEX ix_legacy_agent_context_checkpoints_thread_created "
        "RENAME TO ix_agent_context_checkpoints_thread_created"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT fk_legacy_agent_context_checkpoints_run_id_agent_runs "
        "TO fk_agent_context_checkpoints_run_id_agent_runs"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT fk_legacy_agent_context_checkpoints_thread_id_agent_threads "
        "TO fk_agent_context_checkpoints_thread_id_agent_threads"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT uq_legacy_agent_context_checkpoints_namespace_id "
        "TO uq_agent_context_checkpoints_namespace_id"
    )
    op.execute(
        "ALTER TABLE legacy_agent_context_checkpoints "
        "RENAME CONSTRAINT pk_legacy_agent_context_checkpoints TO pk_agent_context_checkpoints"
    )
    op.rename_table("legacy_agent_context_checkpoints", "agent_context_checkpoints")
