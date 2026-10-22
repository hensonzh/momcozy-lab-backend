"""Durable nonmedical analysis events; clinical fields remain unimplemented."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0033"
down_revision = "20261022_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stool_analysis_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_id", pg.UUID(as_uuid=True), sa.ForeignKey("stool_analyses.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_id", "sequence", name="uq_stool_analysis_events_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_stool_analysis_events_sequence_positive"),
        sa.CheckConstraint(
            "event_type IN ('stool.card.created','stool.analysis.failed','stool.analysis.cancelled')",
            name="ck_stool_analysis_events_nonmedical_type",
        ),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM stool_analysis_events)")).scalar():
        raise RuntimeError("Stool analysis events exist; preserve private data and roll forward instead.")
    op.drop_table("stool_analysis_events")
