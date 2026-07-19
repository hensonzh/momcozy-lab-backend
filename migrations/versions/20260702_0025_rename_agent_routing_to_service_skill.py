"""rename agent routing fields to service skill fields

Revision ID: 20260702_0025
Revises: 20260702_0024
Create Date: 2026-07-08 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0025"
down_revision = "20260702_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_agent_runs_specialist_started", table_name="agent_runs")
    op.alter_column(
        "agent_runs",
        "specialist_id",
        new_column_name="service_skill_id",
        existing_type=sa.String(length=80),
        existing_nullable=False,
        existing_server_default="",
    )
    op.create_index("ix_agent_runs_service_skill_started", "agent_runs", ["service_skill_id", "started_at"])

    op.drop_index("ix_agent_routing_decisions_specialist_created", table_name="agent_routing_decisions")
    op.alter_column(
        "agent_routing_decisions",
        "primary_specialist_id",
        new_column_name="selected_skill_id",
        existing_type=sa.String(length=80),
        existing_nullable=False,
    )
    op.create_index(
        "ix_agent_routing_decisions_service_skill_created",
        "agent_routing_decisions",
        ["selected_skill_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_routing_decisions_service_skill_created", table_name="agent_routing_decisions")
    op.alter_column(
        "agent_routing_decisions",
        "selected_skill_id",
        new_column_name="primary_specialist_id",
        existing_type=sa.String(length=80),
        existing_nullable=False,
    )
    op.create_index(
        "ix_agent_routing_decisions_specialist_created",
        "agent_routing_decisions",
        ["primary_specialist_id", "created_at"],
    )

    op.drop_index("ix_agent_runs_service_skill_started", table_name="agent_runs")
    op.alter_column(
        "agent_runs",
        "service_skill_id",
        new_column_name="specialist_id",
        existing_type=sa.String(length=80),
        existing_nullable=False,
        existing_server_default="",
    )
    op.create_index("ix_agent_runs_specialist_started", "agent_runs", ["specialist_id", "started_at"])
