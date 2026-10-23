"""Persist validated stool field events and completed results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0031"
down_revision = "20261022_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stool_analysis_events", sa.Column("payload", pg.JSONB()))
    op.drop_constraint("ck_stool_analysis_events_nonmedical_type", "stool_analysis_events", type_="check")
    op.create_check_constraint(
        "ck_stool_analysis_events_type", "stool_analysis_events",
        "event_type IN ('stool.card.created','stool.analysis.started','stool.field.ready',"
        "'stool.analysis.completed','stool.analysis.failed','stool.analysis.cancelled')",
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM stool_analysis_events "
        "WHERE event_type IN ('stool.field.ready','stool.analysis.completed') OR payload IS NOT NULL)"
    )).scalar():
        raise RuntimeError("Stool analysis results exist; preserve them and roll forward instead.")
    op.drop_constraint("ck_stool_analysis_events_type", "stool_analysis_events", type_="check")
    op.create_check_constraint(
        "ck_stool_analysis_events_nonmedical_type", "stool_analysis_events",
        "event_type IN ('stool.card.created','stool.analysis.started','stool.analysis.failed','stool.analysis.cancelled')",
    )
    op.drop_column("stool_analysis_events", "payload")
