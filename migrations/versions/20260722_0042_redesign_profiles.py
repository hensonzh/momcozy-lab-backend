"""redesign user and infant profiles

Revision ID: 20260722_0042
Revises: 20260721_0041
Create Date: 2026-07-22 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260722_0042"
down_revision = "20260721_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_infant_profiles_owner_status", table_name="infant_profiles")
    op.create_index("ix_infant_profiles_owner_deleted_at", "infant_profiles", ["owner_user_id", "deleted_at"])
    op.drop_column("infant_profiles", "status")

    op.drop_column("user_profiles", "lactation_advice")
    op.drop_column("user_profiles", "feeding_advice")
    op.drop_column("user_profiles", "profile_onboarding_skipped_at")
    op.drop_column("user_profiles", "profile_onboarding_completed_at")

    op.drop_index("ix_user_profiles_delivery_date", table_name="user_profiles")
    op.alter_column("user_profiles", "display_name", new_column_name="preferred_name")
    op.alter_column("user_profiles", "delivery_date", new_column_name="estimated_due_date")
    op.alter_column(
        "user_profiles",
        "preferred_name",
        existing_type=sa.String(length=120),
        nullable=True,
        server_default=None,
    )
    op.execute("UPDATE user_profiles SET preferred_name = NULL WHERE btrim(preferred_name) = ''")
    op.execute(
        """
        INSERT INTO user_profiles (id, user_id, preferred_name)
        SELECT md5(u.id::text || '-user-profile')::uuid, u.id, btrim(u.display_name)
        FROM users AS u
        LEFT JOIN user_profiles AS p ON p.user_id = u.id
        WHERE p.user_id IS NULL
          AND btrim(u.display_name) <> ''
          AND btrim(u.display_name) <> 'Momcozy 体验用户'
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE user_profiles AS p
        SET preferred_name = btrim(u.display_name)
        FROM users AS u
        WHERE p.user_id = u.id
          AND (p.preferred_name IS NULL OR btrim(p.preferred_name) = '')
          AND btrim(u.display_name) <> ''
          AND btrim(u.display_name) <> 'Momcozy 体验用户'
        """
    )
    op.drop_column("users", "display_name")
    op.create_index("ix_user_profiles_estimated_due_date", "user_profiles", ["estimated_due_date"])

    op.alter_column("infant_profiles", "infant_name", new_column_name="name")
    op.alter_column("infant_profiles", "sex", new_column_name="sex_at_birth")
    op.alter_column(
        "infant_profiles",
        "sex_at_birth",
        existing_type=sa.String(length=32),
        nullable=True,
        server_default=None,
    )
    op.execute(
        """
        UPDATE infant_profiles
        SET sex_at_birth = CASE
            WHEN sex_at_birth IS NULL OR btrim(sex_at_birth) = '' THEN NULL
            WHEN lower(btrim(sex_at_birth)) IN ('female', 'f', 'woman', 'girl', '女', '女性', '女孩') THEN 'female'
            WHEN lower(btrim(sex_at_birth)) IN ('male', 'm', 'man', 'boy', '男', '男性', '男孩') THEN 'male'
            WHEN lower(btrim(sex_at_birth)) IN ('intersex', 'i', '双性', '间性') THEN 'intersex'
            WHEN lower(btrim(sex_at_birth)) IN ('undisclosed', 'not disclosed', 'prefer not to say', '未透露', '不愿透露')
                THEN 'undisclosed'
            WHEN lower(btrim(sex_at_birth)) IN ('unknown', 'u', '未知', '不确定') THEN 'unknown'
            ELSE 'unknown'
        END
        """
    )


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE users AS u
        SET display_name = COALESCE(p.preferred_name, '')
        FROM user_profiles AS p
        WHERE p.user_id = u.id
        """
    )

    op.execute("UPDATE infant_profiles SET sex_at_birth = '' WHERE sex_at_birth IS NULL")
    op.alter_column(
        "infant_profiles",
        "sex_at_birth",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="",
    )
    op.alter_column("infant_profiles", "sex_at_birth", new_column_name="sex")
    op.alter_column("infant_profiles", "name", new_column_name="infant_name")

    op.drop_index("ix_user_profiles_estimated_due_date", table_name="user_profiles")
    op.execute("UPDATE user_profiles SET preferred_name = '' WHERE preferred_name IS NULL")
    op.alter_column(
        "user_profiles",
        "preferred_name",
        existing_type=sa.String(length=120),
        nullable=False,
        server_default="",
    )
    op.alter_column("user_profiles", "estimated_due_date", new_column_name="delivery_date")
    op.alter_column("user_profiles", "preferred_name", new_column_name="display_name")
    op.create_index("ix_user_profiles_delivery_date", "user_profiles", ["delivery_date"])

    op.add_column(
        "user_profiles",
        sa.Column("profile_onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("profile_onboarding_skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("feeding_advice", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "user_profiles",
        sa.Column("lactation_advice", sa.Text(), nullable=False, server_default=""),
    )

    op.add_column(
        "infant_profiles",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.drop_index("ix_infant_profiles_owner_deleted_at", table_name="infant_profiles")
    op.create_index("ix_infant_profiles_owner_status", "infant_profiles", ["owner_user_id", "status"])
