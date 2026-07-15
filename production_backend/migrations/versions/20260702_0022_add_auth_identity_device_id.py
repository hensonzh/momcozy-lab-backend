"""add device id to auth identities

Revision ID: 20260702_0022
Revises: 20260702_0021
Create Date: 2026-07-02 03:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0022"
down_revision = "20260702_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_identities",
        sa.Column("device_id", sa.String(length=120), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE auth_identities
        SET device_id = split_part(subject, ':', 2)
        WHERE provider = 'invite'
          AND device_id = ''
          AND position(':' in subject) > 0
        """
    )


def downgrade() -> None:
    op.drop_column("auth_identities", "device_id")
