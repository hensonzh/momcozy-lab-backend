"""Persist Me concerns, order, voluntary profile context and mother observations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20260920_0021"
down_revision = "20260920_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "me_preferences",
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("profile", pg.JSONB(), nullable=False),
        sa.Column("concerns", pg.JSONB(), nullable=False),
        sa.Column("record_order", pg.JSONB(), nullable=False),
    )
    op.create_table(
        "mother_observations",
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.String(120), nullable=False),
        sa.Column("fields", pg.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mother_observations_occurred_at", "mother_observations", ["occurred_at"])


def downgrade():
    # Do not silently discard observations or voluntary profile data.
    connection = op.get_bind()
    for table in ["mother_observations", "me_preferences"]:
        if connection.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("Me data exists; archive it explicitly before downgrading")
    op.drop_table("mother_observations")
    op.drop_table("me_preferences")
