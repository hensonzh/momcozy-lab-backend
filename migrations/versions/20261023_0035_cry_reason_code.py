"""Preserve a bounded classifier protocol reason without recording audio."""
from alembic import op
import sqlalchemy as sa

revision = '20261023_0035'
down_revision = '20261022_0034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('cry_analyses', sa.Column('reason_code', sa.String(64), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text('SELECT EXISTS (SELECT 1 FROM cry_analyses WHERE reason_code IS NOT NULL)')).scalar():
        raise RuntimeError('Cry service reason codes exist; preserve them and roll forward instead.')
    op.drop_column('cry_analyses', 'reason_code')
