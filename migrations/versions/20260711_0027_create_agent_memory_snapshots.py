"""create agent memory snapshots

Revision ID: 20260711_0027
Revises: 20260702_0026
Create Date: 2026-07-11 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260711_0027"
down_revision = "20260702_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_memories", sa.Column("memory_key", sa.String(length=120), nullable=True))
    op.create_unique_constraint(
        "uq_agent_memories_owner_memory_key",
        "agent_memories",
        ["owner_user_id", "memory_key"],
    )
    op.create_table(
        "agent_memory_snapshots",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False, server_default="v1"),
        sa.Column("items_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("extractor_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_agent_memory_snapshots_owner_user_id_users",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_agent_memory_snapshots"),
    )


def downgrade() -> None:
    op.drop_table("agent_memory_snapshots")
    op.drop_constraint("uq_agent_memories_owner_memory_key", "agent_memories", type_="unique")
    op.drop_column("agent_memories", "memory_key")
