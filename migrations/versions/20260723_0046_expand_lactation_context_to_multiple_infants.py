"""expand lactation context to multiple infants

Revision ID: 20260723_0046
Revises: 20260723_0045
Create Date: 2026-07-23 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260723_0046"
down_revision = "20260723_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        sa.ForeignKeyConstraint(
            ["infant_id"],
            ["infant_profiles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["maternal_lactation_profile_id"],
            ["maternal_lactation_profiles.id"],
        ),
        sa.PrimaryKeyConstraint(
            "maternal_lactation_profile_id",
            "infant_id",
        ),
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
        INSERT INTO maternal_lactation_profile_infants (
            maternal_lactation_profile_id,
            infant_id,
            birth_order
        )
        SELECT id, current_infant_id, 1
        FROM maternal_lactation_profiles
        WHERE current_infant_id IS NOT NULL
        """
    )
    op.drop_index(
        "ix_maternal_lactation_profiles_current_infant_id",
        table_name="maternal_lactation_profiles",
    )
    op.drop_column("maternal_lactation_profiles", "current_infant_id")


def downgrade() -> None:
    op.add_column(
        "maternal_lactation_profiles",
        sa.Column(
            "current_infant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        None,
        "maternal_lactation_profiles",
        "infant_profiles",
        ["current_infant_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE maternal_lactation_profiles AS profile
        SET current_infant_id = link.infant_id
        FROM maternal_lactation_profile_infants AS link
        WHERE link.maternal_lactation_profile_id = profile.id
          AND link.birth_order = 1
        """
    )
    op.create_index(
        "ix_maternal_lactation_profiles_current_infant_id",
        "maternal_lactation_profiles",
        ["current_infant_id"],
    )
    op.drop_table("maternal_lactation_profile_infants")
