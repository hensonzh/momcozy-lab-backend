"""Track authenticated onboarding completion and retry identity."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20260926_0023"
down_revision = "20260926_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "onboarding_confirmations",
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("primary_infant_id", pg.UUID(as_uuid=True), sa.ForeignKey("baby_profiles.id"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM onboarding_confirmations)")).scalar():
        raise RuntimeError("Onboarding confirmations exist; preserve them and roll forward instead.")
    op.drop_table("onboarding_confirmations")
