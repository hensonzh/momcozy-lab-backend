"""create notifications and support tickets

Revision ID: 20260702_0011
Revises: 20260702_0010
Create Date: 2026-07-02 02:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0011"
down_revision = "20260702_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unread"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_notifications_owner_user_id_users"),
    )
    op.create_index(
        "ix_notifications_owner_status_created",
        "notifications",
        ["owner_user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_notifications_owner_type_created",
        "notifications",
        ["owner_user_id", "notification_type", "created_at"],
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_number", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="submitted"),
        sa.Column("issue_type", sa.String(length=120), nullable=False, server_default="other"),
        sa.Column("issue_summary", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("product_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("order_number", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("purchase_channel", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("user_contact", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("urgency", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="agent"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_support_tickets_owner_user_id_users"),
        sa.UniqueConstraint("ticket_number", name="uq_support_tickets_ticket_number"),
    )
    op.create_index(
        "ix_support_tickets_owner_status_updated",
        "support_tickets",
        ["owner_user_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_support_tickets_owner_created",
        "support_tickets",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_owner_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_owner_status_updated", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index("ix_notifications_owner_type_created", table_name="notifications")
    op.drop_index("ix_notifications_owner_status_created", table_name="notifications")
    op.drop_table("notifications")
