"""care documentation

Revision ID: 20260908_0008
Revises: 20260908_0007
Create Date: 2026-09-08 11:19:22.459379+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260908_0008'
down_revision = '20260908_0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('care_clinical_notes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('consultation_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('revises_id', sa.UUID(), nullable=True),
    sa.Column('amendment_reason', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'signed') = (signed_at IS NOT NULL)", name=op.f('ck_care_clinical_notes_clinical_note_signature')),
    sa.CheckConstraint("status IN ('draft','signed')", name=op.f('ck_care_clinical_notes_clinical_note_status')),
    sa.CheckConstraint('revision >= 1 AND version >= 1', name=op.f('ck_care_clinical_notes_clinical_note_versions')),
    sa.ForeignKeyConstraint(['author_id'], ['care_providers.user_id'], name=op.f('fk_care_clinical_notes_author_id_care_providers')),
    sa.ForeignKeyConstraint(['consultation_id'], ['care_consultations.id'], name=op.f('fk_care_clinical_notes_consultation_id_care_consultations')),
    sa.ForeignKeyConstraint(['revises_id'], ['care_clinical_notes.id'], name=op.f('fk_care_clinical_notes_revises_id_care_clinical_notes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_clinical_notes')),
    sa.UniqueConstraint('consultation_id', 'revision', name=op.f('uq_care_clinical_notes_consultation_id'))
    )
    op.create_index('uq_care_clinical_note_draft', 'care_clinical_notes', ['consultation_id'], unique=True, postgresql_where=sa.text("status = 'draft'"), sqlite_where=sa.text("status = 'draft'"))
    op.create_table('care_plan_drafts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('consultation_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('published_revision', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version >= 1 AND published_revision >= 0', name=op.f('ck_care_plan_drafts_care_plan_draft_versions')),
    sa.ForeignKeyConstraint(['author_id'], ['care_providers.user_id'], name=op.f('fk_care_plan_drafts_author_id_care_providers')),
    sa.ForeignKeyConstraint(['consultation_id'], ['care_consultations.id'], name=op.f('fk_care_plan_drafts_consultation_id_care_consultations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_plan_drafts')),
    sa.UniqueConstraint('consultation_id', name=op.f('uq_care_plan_drafts_consultation_id'))
    )
    op.create_table('care_plan_publications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('signed_note_id', sa.UUID(), nullable=False),
    sa.Column('publisher_id', sa.UUID(), nullable=False),
    sa.Column('publisher_name', sa.String(length=120), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_care_plan_publications_care_plan_publication_revision')),
    sa.ForeignKeyConstraint(['plan_id'], ['care_plan_drafts.id'], name=op.f('fk_care_plan_publications_plan_id_care_plan_drafts')),
    sa.ForeignKeyConstraint(['publisher_id'], ['care_providers.user_id'], name=op.f('fk_care_plan_publications_publisher_id_care_providers')),
    sa.ForeignKeyConstraint(['signed_note_id'], ['care_clinical_notes.id'], name=op.f('fk_care_plan_publications_signed_note_id_care_clinical_notes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_plan_publications')),
    sa.UniqueConstraint('plan_id', 'revision', name=op.f('uq_care_plan_publications_plan_id'))
    )
    op.create_table('care_task_progress',
    sa.Column('publication_id', sa.UUID(), nullable=False),
    sa.Column('source_key', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','in_progress','completed','skipped')", name=op.f('ck_care_task_progress_care_task_status')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_care_task_progress_care_task_version')),
    sa.ForeignKeyConstraint(['publication_id'], ['care_plan_publications.id'], name=op.f('fk_care_task_progress_publication_id_care_plan_publications')),
    sa.PrimaryKeyConstraint('publication_id', 'source_key', name=op.f('pk_care_task_progress'))
    )


def downgrade() -> None:
    op.drop_table('care_task_progress')
    op.drop_table('care_plan_publications')
    op.drop_table('care_plan_drafts')
    op.drop_index('uq_care_clinical_note_draft', table_name='care_clinical_notes', postgresql_where=sa.text("status = 'draft'"), sqlite_where=sa.text("status = 'draft'"))
    op.drop_table('care_clinical_notes')
