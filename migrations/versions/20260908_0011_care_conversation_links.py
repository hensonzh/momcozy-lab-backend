"""care conversation links

Revision ID: 20260908_0011
Revises: 20260908_0010
Create Date: 2026-09-08 13:48:27.396512+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '20260908_0011'
down_revision = '20260908_0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('care_conversation_links',
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('shared_since', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['care_episodes.id'], name=op.f('fk_care_conversation_links_episode_id_care_episodes')),
    sa.PrimaryKeyConstraint('thread_id', name=op.f('pk_care_conversation_links'))
    )
    op.create_index('ix_care_conversations_episode_shared', 'care_conversation_links', ['episode_id', 'shared_since'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_care_conversations_episode_shared', table_name='care_conversation_links')
    op.drop_table('care_conversation_links')
