"""create stable per-thread agent image URL bindings

Revision ID: 20260721_0040
Revises: 20260720_0039
Create Date: 2026-07-21 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260721_0040"
down_revision = "20260720_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_image_accesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_image_accesses_thread_id_agent_threads",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["files.id"],
            name="fk_agent_image_accesses_asset_id_files",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "asset_id",
            name="uq_agent_image_accesses_thread_asset",
        ),
    )
    op.create_index(
        "ix_agent_image_accesses_thread_asset",
        "agent_image_accesses",
        ["thread_id", "asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_image_accesses_thread_asset", table_name="agent_image_accesses")
    op.drop_table("agent_image_accesses")
