"""Add dated, atomic baby daily status observations without altering existing records."""
from alembic import op

revision = '20260920_0020'
down_revision = '20260916_0019'
branch_labels = None
depends_on = None


def _constraints(daily: bool) -> None:
    suffix = ",'daily_status'" if daily else ''
    op.drop_constraint(op.f('ck_baby_records_baby_record_kind'), 'baby_records', type_='check')
    op.drop_constraint(op.f('ck_baby_records_baby_record_time_kind'), 'baby_records', type_='check')
    op.create_check_constraint('baby_record_kind', 'baby_records',
        f"kind IN ('feeding','sleep','diaper','growth','development'{suffix})")
    op.create_check_constraint('baby_record_time_kind', 'baby_records',
        f"(kind IN ('growth','development'{suffix}) AND recorded_on IS NOT NULL AND occurred_at IS NULL) OR (kind IN ('feeding','sleep','diaper') AND occurred_at IS NOT NULL AND recorded_on IS NULL)")


def upgrade() -> None:
    _constraints(True)


def downgrade() -> None:
    # Refuse downgrade while new observations exist; never silently delete user data.
    _constraints(False)
