"""create plans and plan tasks

Revision ID: 20260702_0008
Revises: 20260702_0007
Create Date: 2026-07-02 01:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0008"
down_revision = "20260702_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_plans_owner_user_id_users"),
    )
    op.create_index("ix_plans_owner_status_updated", "plans", ["owner_user_id", "status", "updated_at"])
    op.create_index("ix_plans_owner_type", "plans", ["owner_user_id", "plan_type"])

    op.create_table(
        "plan_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_date", sa.Date(), nullable=True),
        sa.Column("task_time", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_plan_tasks_owner_user_id_users"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_tasks_plan_id_plans"),
    )
    op.create_index("ix_plan_tasks_owner_date_status", "plan_tasks", ["owner_user_id", "task_date", "status"])
    op.create_index("ix_plan_tasks_plan_id", "plan_tasks", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_tasks_plan_id", table_name="plan_tasks")
    op.drop_index("ix_plan_tasks_owner_date_status", table_name="plan_tasks")
    op.drop_table("plan_tasks")
    op.drop_index("ix_plans_owner_type", table_name="plans")
    op.drop_index("ix_plans_owner_status_updated", table_name="plans")
    op.drop_table("plans")
