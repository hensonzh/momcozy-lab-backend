"""create device sessions and refresh tokens

Revision ID: 20260702_0004
Revises: 20260702_0003
Create Date: 2026-07-02 00:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0004"
down_revision = "20260702_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("user_agent_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("ip_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_device_sessions_user_id_users"),
    )
    op.create_index("ix_device_sessions_user_status", "device_sessions", ["user_id", "status"])
    op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["session_id"], ["device_sessions.id"], name="fk_refresh_tokens_session_id_device_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_session_status", "refresh_tokens", ["session_id", "status"])
    op.create_index("ix_refresh_tokens_family_status", "refresh_tokens", ["family_id", "status"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_status", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_session_status", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_device_sessions_device_id", table_name="device_sessions")
    op.drop_index("ix_device_sessions_user_status", table_name="device_sessions")
    op.drop_table("device_sessions")
