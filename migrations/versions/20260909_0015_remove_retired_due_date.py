"""Remove the retired prenatal due-date field from user profiles.

Revision ID: 20260909_0015
Revises: 20260908_0014
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0015"
down_revision = "20260908_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The column was nullable and has no current product consumer. Dropping it
    # prevents old prenatal facts from reappearing in profile or Agent DTOs.
    op.drop_index("ix_user_profiles_estimated_due_date", table_name="user_profiles")
    op.drop_column("user_profiles", "estimated_due_date")


def downgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("estimated_due_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_user_profiles_estimated_due_date",
        "user_profiles",
        ["estimated_due_date"],
        unique=False,
    )
