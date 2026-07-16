"""rename the agent runtime version contract

Revision ID: 20260716_0036
Revises: 20260716_0035
Create Date: 2026-07-16 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0036"
down_revision = "20260716_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "graph_version",
        new_column_name="runtime_version",
        existing_type=sa.String(length=80),
        existing_nullable=False,
        existing_server_default="",
    )
    op.execute("UPDATE agent_runs SET runtime_pattern = 'sdk_only' WHERE runtime_pattern = 'langgraph_sdk'")
    op.alter_column(
        "agent_runs",
        "runtime_pattern",
        existing_type=sa.String(length=64),
        existing_nullable=False,
        server_default="sdk_only",
    )


def downgrade() -> None:
    op.execute("UPDATE agent_runs SET runtime_pattern = 'langgraph_sdk' WHERE runtime_pattern = 'sdk_only'")
    op.alter_column(
        "agent_runs",
        "runtime_pattern",
        existing_type=sa.String(length=64),
        existing_nullable=False,
        server_default="langgraph_sdk",
    )
    op.alter_column(
        "agent_runs",
        "runtime_version",
        new_column_name="graph_version",
        existing_type=sa.String(length=80),
        existing_nullable=False,
        existing_server_default="",
    )
