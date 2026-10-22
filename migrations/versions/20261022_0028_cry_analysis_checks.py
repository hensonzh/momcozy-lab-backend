"""Enforce the pending/failed status contract on databases already at 0027.

A local pilot may have applied 0027 before its checks included the asynchronous
states. Recreate the checks on both pilot and fresh installs without data loss.
"""
from alembic import op

revision = '20261022_0028'
down_revision = '20261022_0027'
branch_labels = None
depends_on = None

STATUS = 'ck_cry_analyses_ck_cry_analyses_status'
ANSWER = 'ck_cry_analyses_ck_cry_analyses_answer'


def upgrade() -> None:
    op.drop_constraint(op.f(ANSWER), 'cry_analyses', type_='check')
    op.drop_constraint(op.f(STATUS), 'cry_analyses', type_='check')
    op.create_check_constraint(op.f(STATUS), 'cry_analyses', "status IN ('pending', 'done', 'incomplete', 'failed')")
    op.create_check_constraint(op.f(ANSWER), 'cry_analyses',
        "(status IN ('pending', 'incomplete', 'failed') AND answer IS NULL) OR "
        "(status = 'done' AND answer IN ('sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'))")


def downgrade() -> None:
    # The checked-in 0027 already declares these exact checks. Keep the schema
    # equivalent when moving only the revision marker back to 0027.
    pass
