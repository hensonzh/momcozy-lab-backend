"""create agent safety and eval tables

Revision ID: 20260702_0013
Revises: 20260702_0012
Create Date: 2026-07-02 03:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0013"
down_revision = "20260702_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_safety_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_safety_events_owner_user_id_users"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], name="fk_agent_safety_events_run_id_agent_runs"),
    )
    op.create_index("ix_agent_safety_events_run_created", "agent_safety_events", ["run_id", "created_at"])
    op.create_index(
        "ix_agent_safety_events_owner_category_created",
        "agent_safety_events",
        ["owner_user_id", "category", "created_at"],
    )

    op.create_table(
        "agent_eval_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("suite", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("input_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected_behavior_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected_tool_calls_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expected_safety_decision", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("owner_team", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], name="fk_agent_eval_cases_source_run_id_agent_runs"),
    )
    op.create_index("ix_agent_eval_cases_suite_status", "agent_eval_cases", ["suite", "status"])
    op.create_index("ix_agent_eval_cases_domain_status", "agent_eval_cases", ["domain", "status"])


def downgrade() -> None:
    op.drop_index("ix_agent_eval_cases_domain_status", table_name="agent_eval_cases")
    op.drop_index("ix_agent_eval_cases_suite_status", table_name="agent_eval_cases")
    op.drop_table("agent_eval_cases")
    op.drop_index("ix_agent_safety_events_owner_category_created", table_name="agent_safety_events")
    op.drop_index("ix_agent_safety_events_run_created", table_name="agent_safety_events")
    op.drop_table("agent_safety_events")
