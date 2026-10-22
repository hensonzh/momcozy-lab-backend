"""Keep stool analysis context, execution queue and event history in Product."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0030"
down_revision = "20261022_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stool_analyses", sa.Column("baby_context_snapshot_id", pg.UUID(as_uuid=True)))
    op.add_column("stool_analyses", sa.Column("market", sa.String(2)))
    op.add_column("stool_analyses", sa.Column("language", sa.String(2)))
    op.add_column("stool_analyses", sa.Column("baby_profile_version", sa.Integer()))
    op.add_column("stool_analyses", sa.Column("baby_birth_date", sa.Date()))
    op.add_column("stool_analyses", sa.Column("baby_feeding_mode", sa.String(32)))
    op.add_column("stool_analyses", sa.Column("context_captured_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_stool_analyses_baby_context_snapshot_id", "stool_analyses", ["baby_context_snapshot_id"])
    op.create_check_constraint("ck_stool_analyses_market", "stool_analyses", "market IS NULL OR market IN ('CN','US')")
    op.create_check_constraint("ck_stool_analyses_language", "stool_analyses", "language IS NULL OR language IN ('zh','en')")
    op.create_check_constraint(
        "ck_stool_analyses_context_snapshot_complete", "stool_analyses",
        "baby_context_snapshot_id IS NULL OR (market IS NOT NULL AND language IS NOT NULL "
        "AND baby_profile_version >= 1 AND baby_feeding_mode IS NOT NULL AND context_captured_at IS NOT NULL)",
    )

    op.add_column("stool_analyses", sa.Column("run_attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("stool_analyses", sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("stool_analyses", sa.Column("run_lease_until", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_stool_analyses_run_attempt_nonnegative", "stool_analyses", "run_attempt >= 0")
    op.create_index("ix_stool_analyses_run_due", "stool_analyses", ["market", "status", "run_after"])

    # Existing synthetic jobs lack a trusted photo/context snapshot. Never dispatch them.
    op.execute(sa.text("""INSERT INTO stool_analysis_events (id, analysis_id, sequence, schema_version, event_type)
        SELECT gen_random_uuid(), a.id, 2, 'stool.events.v1', 'stool.analysis.failed'
        FROM stool_analyses a
        WHERE a.status IN ('queued','running') AND a.deleted_at IS NULL AND a.revoked_at IS NULL
          AND EXISTS (SELECT 1 FROM stool_analysis_events e WHERE e.analysis_id=a.id AND e.sequence=1)
          AND NOT EXISTS (SELECT 1 FROM stool_analysis_events e WHERE e.analysis_id=a.id AND e.sequence=2)"""))
    op.execute(sa.text("UPDATE stool_analyses SET status='failed' WHERE status IN ('queued','running')"))
    op.drop_constraint("ck_stool_analysis_events_nonmedical_type", "stool_analysis_events", type_="check")
    op.create_check_constraint(
        "ck_stool_analysis_events_nonmedical_type", "stool_analysis_events",
        "event_type IN ('stool.card.created','stool.analysis.started','stool.analysis.failed','stool.analysis.cancelled')",
    )


def downgrade() -> None:
    raise RuntimeError("Stool task history must be preserved; roll forward instead.")
