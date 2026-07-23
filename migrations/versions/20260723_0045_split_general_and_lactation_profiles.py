"""split general maternal facts from lactation-specific state

Revision ID: 20260723_0045
Revises: 20260723_0044
Create Date: 2026-07-23 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260723_0045"
down_revision = "20260723_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maternal_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("delivery_count", sa.Integer(), nullable=True),
        sa.Column("latest_delivery_method", sa.String(length=32), nullable=True),
        sa.Column("latest_delivery_date", sa.Date(), nullable=True),
        sa.Column("has_cesarean_history", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "delivery_count IS NULL OR (delivery_count >= 1 AND delivery_count <= 20)",
            name="ck_maternal_profiles_delivery_count",
        ),
        sa.CheckConstraint(
            "latest_delivery_method IS NULL OR latest_delivery_method IN ('vaginal', 'cesarean', 'assisted_vaginal', 'other', 'unknown')",
            name="ck_maternal_profiles_delivery_method",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            name="uq_maternal_profiles_owner_user_id",
        ),
    )
    op.create_table(
        "lactation_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("current_feeding_mode", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_feeding_mode IS NULL OR current_feeding_mode IN "
            "('exclusive_breastfeeding', 'expressed_milk_feeding', "
            "'mixed_feeding', 'formula_feeding', 'unknown')",
            name="ck_lactation_profiles_feeding_mode",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            name="uq_lactation_profiles_owner_user_id",
        ),
    )
    op.create_table(
        "maternal_current_delivery_infants",
        sa.Column(
            "maternal_profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "infant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("birth_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "birth_order >= 1 AND birth_order <= 10",
            name="ck_maternal_current_delivery_infants_birth_order",
        ),
        sa.ForeignKeyConstraint(["infant_id"], ["infant_profiles.id"]),
        sa.ForeignKeyConstraint(
            ["maternal_profile_id"],
            ["maternal_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("maternal_profile_id", "infant_id"),
        sa.UniqueConstraint(
            "maternal_profile_id",
            "birth_order",
            name="uq_maternal_current_delivery_infants_birth_order",
        ),
        sa.UniqueConstraint(
            "infant_id",
            name="uq_maternal_current_delivery_infants_infant_id",
        ),
    )

    op.execute(
        """
        INSERT INTO maternal_profiles (
            id,
            owner_user_id,
            delivery_count,
            latest_delivery_method,
            latest_delivery_date,
            has_cesarean_history,
            created_at,
            updated_at
        )
        SELECT
            id,
            owner_user_id,
            delivery_count,
            current_delivery_method,
            actual_delivery_date,
            has_cesarean_history,
            created_at,
            updated_at
        FROM maternal_lactation_profiles
        """
    )
    op.execute(
        """
        INSERT INTO lactation_profiles (
            id,
            owner_user_id,
            current_feeding_mode,
            created_at,
            updated_at
        )
        SELECT
            id,
            owner_user_id,
            current_feeding_mode,
            created_at,
            updated_at
        FROM maternal_lactation_profiles
        """
    )
    op.execute(
        """
        INSERT INTO maternal_current_delivery_infants (
            maternal_profile_id,
            infant_id,
            birth_order,
            created_at
        )
        SELECT
            maternal_lactation_profile_id,
            infant_id,
            birth_order,
            created_at
        FROM maternal_lactation_profile_infants
        """
    )

    op.drop_table("maternal_lactation_profile_infants")
    op.drop_table("maternal_lactation_profiles")


def downgrade() -> None:
    op.create_table(
        "maternal_lactation_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("delivery_count", sa.Integer(), nullable=True),
        sa.Column("current_delivery_method", sa.String(length=32), nullable=True),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column("has_cesarean_history", sa.Boolean(), nullable=True),
        sa.Column("current_feeding_mode", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "delivery_count IS NULL OR (delivery_count >= 1 AND delivery_count <= 20)",
            name="ck_maternal_lactation_profiles_delivery_count",
        ),
        sa.CheckConstraint(
            "current_delivery_method IS NULL OR current_delivery_method IN ('vaginal', 'cesarean', 'assisted_vaginal', 'other', 'unknown')",
            name="ck_maternal_lactation_profiles_delivery_method",
        ),
        sa.CheckConstraint(
            "current_feeding_mode IS NULL OR current_feeding_mode IN "
            "('exclusive_breastfeeding', 'expressed_milk_feeding', "
            "'mixed_feeding', 'formula_feeding', 'unknown')",
            name="ck_maternal_lactation_profiles_feeding_mode",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            name="uq_maternal_lactation_profiles_owner_user_id",
        ),
    )
    op.create_table(
        "maternal_lactation_profile_infants",
        sa.Column(
            "maternal_lactation_profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "infant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("birth_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "birth_order >= 1 AND birth_order <= 10",
            name="ck_maternal_lactation_profile_infants_birth_order",
        ),
        sa.ForeignKeyConstraint(["infant_id"], ["infant_profiles.id"]),
        sa.ForeignKeyConstraint(
            ["maternal_lactation_profile_id"],
            ["maternal_lactation_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("maternal_lactation_profile_id", "infant_id"),
        sa.UniqueConstraint(
            "maternal_lactation_profile_id",
            "birth_order",
            name="uq_maternal_lactation_profile_infants_birth_order",
        ),
        sa.UniqueConstraint(
            "infant_id",
            name="uq_maternal_lactation_profile_infants_infant_id",
        ),
    )

    op.execute(
        """
        INSERT INTO maternal_lactation_profiles (
            id,
            owner_user_id,
            delivery_count,
            current_delivery_method,
            actual_delivery_date,
            has_cesarean_history,
            current_feeding_mode,
            created_at,
            updated_at
        )
        SELECT
            COALESCE(maternal.id, lactation.id),
            COALESCE(maternal.owner_user_id, lactation.owner_user_id),
            maternal.delivery_count,
            maternal.latest_delivery_method,
            maternal.latest_delivery_date,
            maternal.has_cesarean_history,
            lactation.current_feeding_mode,
            COALESCE(maternal.created_at, lactation.created_at),
            GREATEST(maternal.updated_at, lactation.updated_at)
        FROM maternal_profiles AS maternal
        FULL OUTER JOIN lactation_profiles AS lactation
          ON lactation.owner_user_id = maternal.owner_user_id
        """
    )
    op.execute(
        """
        INSERT INTO maternal_lactation_profile_infants (
            maternal_lactation_profile_id,
            infant_id,
            birth_order,
            created_at
        )
        SELECT
            maternal_profile_id,
            infant_id,
            birth_order,
            created_at
        FROM maternal_current_delivery_infants
        """
    )

    op.drop_table("maternal_current_delivery_infants")
    op.drop_table("lactation_profiles")
    op.drop_table("maternal_profiles")
