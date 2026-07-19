"""add agent run queue index

Revision ID: 20260702_0024
Revises: 20260702_0023
Create Date: 2026-07-07 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0024"
down_revision = "20260702_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_runs_runnable_created",
        "agent_runs",
        ["status", "created_at", "id"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_runnable_created", table_name="agent_runs")
