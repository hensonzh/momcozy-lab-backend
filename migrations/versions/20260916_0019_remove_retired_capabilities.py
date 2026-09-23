"""Remove diary storage and retired prenatal plans.

Downgrade restores empty tables only; deleted records require a backup.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260916_0019"
down_revision = "20260910_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep actual feeding/pumping facts while removing their retired schedule links.
    retired_tasks = """SELECT t.id FROM plan_tasks t LEFT JOIN plans p ON p.id = t.plan_id
        WHERE p.plan_type IN ('pregnancy', 'birth_prep', 'birth_journey')
           OR t.payload_json ->> 'domain' = 'pregnancy'
           OR t.payload_json ->> 'source' = 'pregnancy_plan'"""
    for table in ("feeding_records", "pumping_records"):
        op.execute(sa.text(f"UPDATE {table} SET plan_task_id = NULL WHERE plan_task_id IN ({retired_tasks})"))
    op.execute(sa.text(f"DELETE FROM plan_tasks WHERE id IN ({retired_tasks})"))
    op.execute("DELETE FROM plans WHERE plan_type IN ('pregnancy', 'birth_prep', 'birth_journey')")
    op.drop_index("uq_plans_owner_active_pregnancy", table_name="plans")
    # Old report snapshots use the removed source schema. Invalidate derived content
    # rather than presenting conclusions whose source records no longer exist.
    op.execute("""UPDATE care_reports SET status = 'cancelled', snapshot = NULL, result = NULL,
        lease_token = NULL, lease_until = NULL, error_code = 'retired_source_removed'
        WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(snapshot -> 'input' -> 'sources', '[]'::jsonb)) source
                      WHERE source ->> 'kind' = 'mother_diary')""")
    op.drop_table("mother_diary_entries")
    op.drop_table("diary_entries")


def downgrade() -> None:
    op.create_table('diary_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('content', sa.Text(), server_default='', nullable=False),
    sa.Column('attributes_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('attachments_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_diary_entries_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_diary_entries')),
    sa.UniqueConstraint('owner_user_id', 'entry_date', name='uq_diary_entries_owner_date')
    )
    op.create_index('ix_diary_entries_owner_date', 'diary_entries', ['owner_user_id', 'entry_date'], unique=False)
    op.create_table("mother_diary_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("diary", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "entry_date", name="uq_mother_diary_owner_date"))
    op.create_index("uq_plans_owner_active_pregnancy", "plans", ["owner_user_id"], unique=True,
        postgresql_where=sa.text("plan_type = 'pregnancy' AND status = 'active' AND deleted_at IS NULL"))
