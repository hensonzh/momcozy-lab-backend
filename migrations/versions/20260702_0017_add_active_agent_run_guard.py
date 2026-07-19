"""add active agent run guard

Revision ID: 20260702_0017
Revises: 20260702_0016
Create Date: 2026-07-02 11:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0017"
down_revision = "20260702_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_agent_runs_thread_active",
        "agent_runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_confirmation')"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_thread_active", table_name="agent_runs")
