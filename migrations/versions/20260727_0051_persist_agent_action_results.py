"""persist authoritative agent action results

Revision ID: 20260727_0051
Revises: 20260726_0050
Create Date: 2026-07-27 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260727_0051"
down_revision = "20260726_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_actions",
        sa.Column(
            "result_payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_actions", "result_payload_json")
