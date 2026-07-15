"""add schedule task states, plan versions, and record links

Revision ID: 20260712_0030
Revises: 20260711_0029
Create Date: 2026-07-12 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260712_0030"
down_revision = "20260711_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("feeding_records", sa.Column("plan_task_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_feeding_records_plan_task_id_plan_tasks",
        "feeding_records",
        "plan_tasks",
        ["plan_task_id"],
        ["id"],
    )
    op.create_index("ix_feeding_records_plan_task", "feeding_records", ["plan_task_id"])
    op.add_column("pumping_records", sa.Column("plan_task_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_pumping_records_plan_task_id_plan_tasks",
        "pumping_records",
        "plan_tasks",
        ["plan_task_id"],
        ["id"],
    )
    op.create_index("ix_pumping_records_plan_task", "pumping_records", ["plan_task_id"])


def downgrade() -> None:
    op.drop_index("ix_pumping_records_plan_task", table_name="pumping_records")
    op.drop_constraint("fk_pumping_records_plan_task_id_plan_tasks", "pumping_records", type_="foreignkey")
    op.drop_column("pumping_records", "plan_task_id")
    op.drop_index("ix_feeding_records_plan_task", table_name="feeding_records")
    op.drop_constraint("fk_feeding_records_plan_task_id_plan_tasks", "feeding_records", type_="foreignkey")
    op.drop_column("feeding_records", "plan_task_id")
    op.drop_column("plans", "version")
