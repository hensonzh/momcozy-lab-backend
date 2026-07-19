"""add audit actor type

Revision ID: 20260702_0015
Revises: 20260702_0014
Create Date: 2026-07-02 02:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0015"
down_revision = "20260702_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="user"),
    )
    op.add_column(
        "audit_logs",
        sa.Column("actor_service", sa.String(length=120), nullable=False, server_default=""),
    )
    op.create_index("ix_audit_logs_actor_service", "audit_logs", ["actor_service", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_service", table_name="audit_logs")
    op.drop_column("audit_logs", "actor_service")
    op.drop_column("audit_logs", "actor_type")
