"""add agent workflow reply cursor

Revision ID: 20260713_0033
Revises: 20260712_0032
Create Date: 2026-07-13 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0033"
down_revision = "20260712_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_workflow_states",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "agent_workflow_states",
        sa.Column("step_token", sa.String(length=128), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("agent_workflow_states", "step_token")
    op.drop_column("agent_workflow_states", "revision")
