"""create audit and idempotency tables

Revision ID: 20260702_0003
Revises: 20260702_0002
Create Date: 2026-07-02 00:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0003"
down_revision = "20260702_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("request_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default="succeeded"),
        sa.Column("details_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_audit_logs_actor_user_id_users"),
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("response_ref", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_idempotency_keys_actor_user_id_users"),
        sa.UniqueConstraint("actor_user_id", "scope", "key", name="uq_idempotency_actor_scope_key"),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")
