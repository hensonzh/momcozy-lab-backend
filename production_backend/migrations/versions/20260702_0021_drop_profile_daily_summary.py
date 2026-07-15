"""drop legacy profile daily summary

Revision ID: 20260702_0021
Revises: 20260702_0020
Create Date: 2026-07-06 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0021"
down_revision = "20260702_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user_profiles", "daily_summary")


def downgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("daily_summary", sa.Text(), nullable=False, server_default=""),
    )
