"""Keep chat-first entry distinct from legacy full-profile onboarding."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261015_0026"
down_revision = "20260928_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_first_onboarding",
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('mother', 'father', 'paternal-grandparent', 'maternal-grandparent', 'other-family')",
            name="ck_chat_first_onboarding_role",
        ),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM chat_first_onboarding)")).scalar():
        raise RuntimeError("Chat-first completion exists; preserve it and roll forward instead.")
    op.drop_table("chat_first_onboarding")
