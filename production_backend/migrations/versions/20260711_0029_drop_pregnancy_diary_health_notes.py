"""drop pregnancy diary health notes

Revision ID: 20260711_0029
Revises: 20260711_0028
Create Date: 2026-07-11 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260711_0029"
down_revision = "20260711_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("pregnancy_diary_health_notes")


def downgrade() -> None:
    op.create_table(
        "pregnancy_diary_health_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("user_report", sa.Text(), nullable=False, server_default=""),
        sa.Column("asked_questions_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("known_answers_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("suggestion_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["pregnancy_diary_entries.id"],
            name="fk_pregnancy_diary_health_notes_entry_id_entries",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_pregnancy_diary_health_notes_owner_user_id_users",
        ),
    )
    op.create_index(
        "ix_pregnancy_diary_health_notes_entry",
        "pregnancy_diary_health_notes",
        ["entry_id", "created_at"],
    )
    op.create_index(
        "ix_pregnancy_diary_health_notes_owner_date",
        "pregnancy_diary_health_notes",
        ["owner_user_id", "entry_date"],
    )
