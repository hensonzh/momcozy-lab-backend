"""create agent memory consolidation runs

Revision ID: 20260711_0028
Revises: 20260711_0027
Create Date: 2026-07-11 11:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260711_0028"
down_revision = "20260711_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_consolidation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="extracting"),
        sa.Column("input_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_agent_memory_consolidation_runs_owner_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_memory_consolidation_runs"),
        sa.UniqueConstraint(
            "owner_user_id",
            "source_date",
            "source_hash",
            "extractor_version",
            name="uq_agent_memory_consolidation_source",
        ),
    )
    op.create_index(
        "ix_agent_memory_consolidation_date_status",
        "agent_memory_consolidation_runs",
        ["source_date", "status"],
    )
    op.create_index(
        "ix_agent_memory_consolidation_owner_created",
        "agent_memory_consolidation_runs",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_memory_consolidation_owner_created",
        table_name="agent_memory_consolidation_runs",
    )
    op.drop_index(
        "ix_agent_memory_consolidation_date_status",
        table_name="agent_memory_consolidation_runs",
    )
    op.drop_table("agent_memory_consolidation_runs")
