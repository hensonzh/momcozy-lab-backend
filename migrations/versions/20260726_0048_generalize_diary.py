"""generalize pregnancy diary entries

Revision ID: 20260726_0048
Revises: 20260723_0047
Create Date: 2026-07-26 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260726_0048"
down_revision = "20260723_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("pregnancy_diary_entries", "diary_entries")
    op.execute(
        "ALTER TABLE diary_entries "
        "RENAME CONSTRAINT fk_pregnancy_diary_entries_owner_user_id_users "
        "TO fk_diary_entries_owner_user_id_users"
    )
    op.execute(
        "ALTER TABLE diary_entries "
        "RENAME CONSTRAINT uq_pregnancy_diary_owner_date "
        "TO uq_diary_entries_owner_date"
    )
    op.execute(
        "ALTER INDEX ix_pregnancy_diary_owner_date "
        "RENAME TO ix_diary_entries_owner_date"
    )
    op.add_column(
        "diary_entries",
        sa.Column(
            "attributes_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE diary_entries
        SET attributes_json = jsonb_build_object(
            'gestational_week', gestational_week,
            'mood', mood,
            'energy_level', energy_level,
            'sleep_summary', sleep_summary,
            'fetal_movement', fetal_movement,
            'symptom_tags', symptom_tags_json,
            'appointment_note', appointment_note,
            'nutrition_note', nutrition_note
        )
        """
    )

    for column_name in (
        "gestational_week",
        "mood",
        "energy_level",
        "sleep_summary",
        "fetal_movement",
        "symptom_tags_json",
        "appointment_note",
        "nutrition_note",
    ):
        op.drop_column("diary_entries", column_name)


def downgrade() -> None:
    op.add_column(
        "diary_entries",
        sa.Column(
            "gestational_week",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "diary_entries",
        sa.Column(
            "mood",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "diary_entries",
        sa.Column(
            "energy_level",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    for column_name in (
        "sleep_summary",
        "fetal_movement",
        "appointment_note",
        "nutrition_note",
    ):
        op.add_column(
            "diary_entries",
            sa.Column(
                column_name,
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )
    op.add_column(
        "diary_entries",
        sa.Column(
            "symptom_tags_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE diary_entries
        SET
            gestational_week = COALESCE(attributes_json ->> 'gestational_week', ''),
            mood = COALESCE(attributes_json ->> 'mood', ''),
            energy_level = COALESCE(attributes_json ->> 'energy_level', ''),
            sleep_summary = COALESCE(attributes_json ->> 'sleep_summary', ''),
            fetal_movement = COALESCE(attributes_json ->> 'fetal_movement', ''),
            symptom_tags_json = CASE
                WHEN jsonb_typeof(attributes_json -> 'symptom_tags') = 'array'
                    THEN attributes_json -> 'symptom_tags'
                ELSE '[]'::jsonb
            END,
            appointment_note = COALESCE(attributes_json ->> 'appointment_note', ''),
            nutrition_note = COALESCE(attributes_json ->> 'nutrition_note', '')
        """
    )

    op.drop_column("diary_entries", "attributes_json")
    op.execute(
        "ALTER TABLE diary_entries "
        "RENAME CONSTRAINT uq_diary_entries_owner_date "
        "TO uq_pregnancy_diary_owner_date"
    )
    op.execute(
        "ALTER INDEX ix_diary_entries_owner_date "
        "RENAME TO ix_pregnancy_diary_owner_date"
    )
    op.execute(
        "ALTER TABLE diary_entries "
        "RENAME CONSTRAINT fk_diary_entries_owner_user_id_users "
        "TO fk_pregnancy_diary_entries_owner_user_id_users"
    )
    op.rename_table("diary_entries", "pregnancy_diary_entries")
