"""Persist only allowlisted classification results, never raw recordings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '20261022_0027'
down_revision = '20261015_0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('cry_analyses',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_user_id', pg.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('thread_id', pg.UUID(as_uuid=True)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('answer', sa.String(20)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'done', 'incomplete', 'failed')", name='ck_cry_analyses_status'),
        sa.CheckConstraint("(status IN ('pending', 'incomplete', 'failed') AND answer IS NULL) OR (status = 'done' AND answer IN ('sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'))", name='ck_cry_analyses_answer'),
    )
    op.create_index('ix_cry_analyses_owner_created', 'cry_analyses', ['owner_user_id', 'created_at'])


def downgrade() -> None:
    if op.get_bind().execute(sa.text('SELECT EXISTS (SELECT 1 FROM cry_analyses)')).scalar():
        raise RuntimeError('Cry analysis results exist; preserve them and roll forward instead.')
    op.drop_index('ix_cry_analyses_owner_created', table_name='cry_analyses')
    op.drop_table('cry_analyses')
