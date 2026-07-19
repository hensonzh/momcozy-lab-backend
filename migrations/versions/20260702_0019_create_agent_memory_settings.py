"""create agent memory settings

Revision ID: 20260702_0019
Revises: 20260702_0018
Create Date: 2026-07-02 12:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0019"
down_revision = "20260702_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_settings",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_memory_settings_owner_user_id_users"),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_agent_memory_settings"),
    )
    op.create_index(
        "ix_agent_memory_settings_owner_updated",
        "agent_memory_settings",
        ["owner_user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memory_settings_owner_updated", table_name="agent_memory_settings")
    op.drop_table("agent_memory_settings")
