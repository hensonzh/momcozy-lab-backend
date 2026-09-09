"""Structured postpartum diary, independent of pregnancy diary and milk events."""

from alembic import op
import sqlalchemy as sa

revision = "20260908_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("mother_diary_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("diary", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "entry_date", name="uq_mother_diary_owner_date"))


def downgrade() -> None:
    op.drop_table("mother_diary_entries")
