"""care_reports

Revision ID: 20260908_0012
Revises: 20260908_0011
Create Date: 2026-09-08 14:07:15.862967+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260908_0012'
down_revision = '20260908_0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('care_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('report_date', sa.Date(), nullable=False),
    sa.Column('timezone', sa.String(length=80), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('case_consent_version', sa.Integer(), nullable=False),
    sa.Column('ai_consent_version', sa.Integer(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease_token', sa.UUID(), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.CheckConstraint("purpose IN ('daily','preparation')", name=op.f('ck_care_reports_care_report_purpose')),
    sa.CheckConstraint("status IN ('waiting_for_record','queued','running','ready','failed','cancelled')", name=op.f('ck_care_reports_care_report_status')),
    sa.CheckConstraint('version >= 1 AND attempts >= 0', name=op.f('ck_care_reports_care_report_versions')),
    sa.ForeignKeyConstraint(['episode_id'], ['care_episodes.id'], name=op.f('fk_care_reports_episode_id_care_episodes')),
    sa.ForeignKeyConstraint(['provider_id'], ['care_providers.user_id'], name=op.f('fk_care_reports_provider_id_care_providers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_reports')),
    sa.UniqueConstraint('episode_id', 'purpose', 'report_date', 'version', name='uq_care_report_revision')
    )
    op.create_index('ix_care_reports_work', 'care_reports', ['status', 'available_at', 'lease_until'], unique=False)
    op.create_table('care_report_reviews',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('report_id', sa.UUID(), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('feedback', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(decision = 'confirmed' AND feedback = '') OR (decision = 'feedback' AND length(trim(feedback)) >= 5)", name=op.f('ck_care_report_reviews_care_report_review_feedback')),
    sa.CheckConstraint("decision IN ('confirmed','feedback')", name=op.f('ck_care_report_reviews_care_report_review_decision')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_care_report_reviews_care_report_review_version')),
    sa.ForeignKeyConstraint(['provider_id'], ['care_providers.user_id'], name=op.f('fk_care_report_reviews_provider_id_care_providers')),
    sa.ForeignKeyConstraint(['report_id'], ['care_reports.id'], name=op.f('fk_care_report_reviews_report_id_care_reports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_report_reviews')),
    sa.UniqueConstraint('report_id', 'version', name='uq_care_report_review_version')
    )


def downgrade() -> None:
    op.drop_table('care_report_reviews')
    op.drop_index('ix_care_reports_work', table_name='care_reports')
    op.drop_table('care_reports')
