"""create pregnancy diary settings

Revision ID: 20260717_0037
Revises: 20260716_0036
Create Date: 2026-07-17 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260717_0037"
down_revision = "20260716_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pregnancy_diary_settings",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auto_capture_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_pregnancy_diary_settings_owner_user_id_users",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_pregnancy_diary_settings"),
    )


def downgrade() -> None:
    op.drop_table("pregnancy_diary_settings")
