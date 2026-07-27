"""apply tree4 product-domain schema changes

Revision ID: 20260727_0049
Revises: 20260727_0048
Create Date: 2026-07-27 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260727_0049"
down_revision = "20260727_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install the final product-domain schema on a reset environment."""

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

    op.create_check_constraint(
        "ck_maternal_profiles_cesarean_history",
        "maternal_profiles",
        "latest_delivery_method <> 'cesarean' "
        "OR has_cesarean_history IS TRUE",
    )

    op.add_column(
        "plans",
        sa.Column("starts_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "plans",
        sa.Column("ends_on", sa.Date(), nullable=True),
    )
    op.create_index(
        "uq_plans_owner_active_pregnancy",
        "plans",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "plan_type = 'pregnancy' "
            "AND status = 'active' "
            "AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_plans_owner_active_pregnancy",
        table_name="plans",
    )
    op.drop_column("plans", "ends_on")
    op.drop_column("plans", "starts_on")

    op.drop_constraint(
        "ck_maternal_profiles_cesarean_history",
        "maternal_profiles",
        type_="check",
    )

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
