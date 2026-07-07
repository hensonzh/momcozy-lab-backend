"""create invite codes

Revision ID: 20260702_0023
Revises: 20260702_0022
Create Date: 2026-07-02 03:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0023"
down_revision = "20260702_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invite_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("assigned_to", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("bound_device_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("bound_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_service", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["bound_user_id"], ["users.id"], name="fk_invite_codes_bound_user_id_users"),
    )
    op.create_index("ix_invite_codes_code", "invite_codes", ["code"], unique=True)
    op.create_index("ix_invite_codes_status", "invite_codes", ["status"])
    op.create_index("ix_invite_codes_created_at", "invite_codes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_invite_codes_created_at", table_name="invite_codes")
    op.drop_index("ix_invite_codes_status", table_name="invite_codes")
    op.drop_index("ix_invite_codes_code", table_name="invite_codes")
    op.drop_table("invite_codes")
