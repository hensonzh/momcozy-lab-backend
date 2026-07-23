"""add lactation analysis context

Revision ID: 20260723_0045
Revises: 20260723_0044
Create Date: 2026-07-23 12:00:00
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
    op.add_column(
        "infant_profiles",
        sa.Column("birth_weight_kg", sa.Float(), nullable=True),
    )
    op.add_column(
        "infant_profiles",
        sa.Column("gestational_age_at_birth_days", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_infant_profiles_birth_weight_kg",
        "infant_profiles",
        "birth_weight_kg IS NULL OR (birth_weight_kg >= 0.2 AND birth_weight_kg <= 10)",
    )
    op.create_check_constraint(
        "ck_infant_profiles_gestational_age_at_birth_days",
        "infant_profiles",
        "gestational_age_at_birth_days IS NULL "
        "OR (gestational_age_at_birth_days >= 140 AND gestational_age_at_birth_days <= 315)",
    )

    op.create_table(
        "maternal_lactation_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "current_infant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
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
            "current_delivery_method IS NULL OR current_delivery_method IN "
            "('vaginal', 'cesarean', 'assisted_vaginal', 'other', 'unknown')",
            name="ck_maternal_lactation_profiles_delivery_method",
        ),
        sa.CheckConstraint(
            "current_feeding_mode IS NULL OR current_feeding_mode IN "
            "('exclusive_breastfeeding', 'expressed_milk_feeding', "
            "'mixed_feeding', 'formula_feeding', 'unknown')",
            name="ck_maternal_lactation_profiles_feeding_mode",
        ),
        sa.ForeignKeyConstraint(
            ["current_infant_id"],
            ["infant_profiles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            name="uq_maternal_lactation_profiles_owner_user_id",
        ),
    )
    op.create_index(
        "ix_maternal_lactation_profiles_current_infant_id",
        "maternal_lactation_profiles",
        ["current_infant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_maternal_lactation_profiles_current_infant_id",
        table_name="maternal_lactation_profiles",
    )
    op.drop_table("maternal_lactation_profiles")

    op.drop_constraint(
        "ck_infant_profiles_gestational_age_at_birth_days",
        "infant_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_infant_profiles_birth_weight_kg",
        "infant_profiles",
        type_="check",
    )
    op.drop_column("infant_profiles", "gestational_age_at_birth_days")
    op.drop_column("infant_profiles", "birth_weight_kg")
