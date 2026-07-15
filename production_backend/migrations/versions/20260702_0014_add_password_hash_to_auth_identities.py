"""add password hash to auth identities

Revision ID: 20260702_0014
Revises: 20260702_0013
Create Date: 2026-07-02 02:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0014"
down_revision = "20260702_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_identities",
        sa.Column("password_hash", sa.String(length=255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("auth_identities", "password_hash")
