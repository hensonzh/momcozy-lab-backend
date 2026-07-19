"""drop retired agent safety gate persistence

Revision ID: 20260716_0034
Revises: 20260713_0033
Create Date: 2026-07-16 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260716_0034"
down_revision = "20260713_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("agent_safety_events")
    op.drop_column("agent_eval_cases", "expected_safety_decision")


def downgrade() -> None:
    op.add_column(
        "agent_eval_cases",
        sa.Column("expected_safety_decision", sa.String(length=64), nullable=False, server_default=""),
    )
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
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_agent_safety_events_owner_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_safety_events_run_id_agent_runs",
        ),
    )
    op.create_index(
        "ix_agent_safety_events_run_created",
        "agent_safety_events",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_safety_events_owner_category_created",
        "agent_safety_events",
        ["owner_user_id", "category", "created_at"],
    )
