"""store one canonical agent tool output

Revision ID: 20260726_0050
Revises: 20260726_0049
Create Date: 2026-07-26 21:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260726_0050"
down_revision = "20260726_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_tool_outputs",
        "safe_output_json",
        new_column_name="output_json",
    )
    op.alter_column(
        "agent_tool_outputs",
        "raw_output_ref",
        new_column_name="output_ref",
    )


def downgrade() -> None:
    op.alter_column(
        "agent_tool_outputs",
        "output_ref",
        new_column_name="raw_output_ref",
    )
    op.alter_column(
        "agent_tool_outputs",
        "output_json",
        new_column_name="safe_output_json",
    )
