"""create pregnancy diary

Revision ID: 20260702_0009
Revises: 20260702_0008
Create Date: 2026-07-02 01:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0009"
down_revision = "20260702_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pregnancy_diary_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("gestational_week", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("mood", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("energy_level", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sleep_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("fetal_movement", sa.Text(), nullable=False, server_default=""),
        sa.Column("symptom_tags_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("appointment_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("nutrition_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("attachments_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_pregnancy_diary_entries_owner_user_id_users"),
        sa.UniqueConstraint("owner_user_id", "entry_date", name="uq_pregnancy_diary_owner_date"),
    )
    op.create_index("ix_pregnancy_diary_owner_date", "pregnancy_diary_entries", ["owner_user_id", "entry_date"])

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
        sa.ForeignKeyConstraint(["entry_id"], ["pregnancy_diary_entries.id"], name="fk_pregnancy_diary_health_notes_entry_id_entries"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_pregnancy_diary_health_notes_owner_user_id_users"),
    )
    op.create_index("ix_pregnancy_diary_health_notes_entry", "pregnancy_diary_health_notes", ["entry_id", "created_at"])
    op.create_index("ix_pregnancy_diary_health_notes_owner_date", "pregnancy_diary_health_notes", ["owner_user_id", "entry_date"])


def downgrade() -> None:
    op.drop_index("ix_pregnancy_diary_health_notes_owner_date", table_name="pregnancy_diary_health_notes")
    op.drop_index("ix_pregnancy_diary_health_notes_entry", table_name="pregnancy_diary_health_notes")
    op.drop_table("pregnancy_diary_health_notes")
    op.drop_index("ix_pregnancy_diary_owner_date", table_name="pregnancy_diary_entries")
    op.drop_table("pregnancy_diary_entries")
