"""create outbox jobs

Revision ID: 20260702_0005
Revises: 20260702_0004
Create Date: 2026-07-02 00:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0005"
down_revision = "20260702_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_jobs_idempotency_key"),
    )
    op.create_index("ix_outbox_jobs_status_next_attempt", "outbox_jobs", ["status", "next_attempt_at"])
    op.create_index("ix_outbox_jobs_locked_until", "outbox_jobs", ["locked_until"])
    op.create_index("ix_outbox_jobs_action_id", "outbox_jobs", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_jobs_action_id", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_locked_until", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_status_next_attempt", table_name="outbox_jobs")
    op.drop_table("outbox_jobs")
