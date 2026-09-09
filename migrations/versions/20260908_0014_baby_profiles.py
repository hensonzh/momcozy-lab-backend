"""Canonical baby profiles; retain UUIDs and all dependent record/service links.

Revision ID: 20260908_0014
Revises: 20260908_0013
"""
from alembic import op
import sqlalchemy as sa

revision = '20260908_0014'
down_revision = '20260908_0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('infant_profiles', 'baby_profiles')
    op.execute('ALTER INDEX ix_infant_profiles_birth_date RENAME TO ix_baby_profiles_birth_date')
    op.execute('ALTER INDEX ix_infant_profiles_owner_deleted_at RENAME TO ix_baby_profiles_owner_deleted_at')
    op.execute('ALTER TABLE baby_profiles RENAME CONSTRAINT pk_infant_profiles TO pk_baby_profiles')
    op.execute('ALTER TABLE baby_profiles RENAME CONSTRAINT fk_infant_profiles_owner_user_id_users TO fk_baby_profiles_owner_user_id_users')
    op.alter_column('baby_profiles', 'sex_at_birth', new_column_name='sex')
    op.execute("UPDATE baby_profiles SET sex = 'unspecified' WHERE sex IS NULL OR sex NOT IN ('female', 'male')")
    op.alter_column('baby_profiles', 'sex', existing_type=sa.String(32), nullable=False)
    op.add_column('baby_profiles', sa.Column('feeding_mode', sa.String(32), nullable=False, server_default='unknown'))
    op.add_column('baby_profiles', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('baby_profiles', 'feeding_mode', server_default=None)
    op.alter_column('baby_profiles', 'version', server_default=None)
    op.create_check_constraint(op.f('ck_baby_profiles_sex'), 'baby_profiles', "sex IN ('female', 'male', 'unspecified')")
    op.create_check_constraint(op.f('ck_baby_profiles_feeding_mode'), 'baby_profiles', "feeding_mode IN ('exclusive_breastfeeding', 'expressed_milk_feeding', 'mixed_feeding', 'formula_feeding', 'unknown')")
    op.create_check_constraint(op.f('ck_baby_profiles_version'), 'baby_profiles', 'version >= 1')
    # These retired profile facts are removed, not turned into invented measurement records.
    op.drop_constraint(op.f('ck_infant_profiles_ck_infant_profiles_birth_weight_kg'), 'baby_profiles', type_='check')
    op.drop_constraint(op.f('ck_infant_profiles_ck_infant_profiles_gestational_age_at_birth_days'), 'baby_profiles', type_='check')
    op.drop_column('baby_profiles', 'birth_weight_kg')
    op.drop_column('baby_profiles', 'gestational_age_at_birth_days')


def downgrade() -> None:
    # Removed profile facts cannot be reconstructed from current measurements.
    op.add_column('baby_profiles', sa.Column('birth_weight_kg', sa.Float(), nullable=True))
    op.add_column('baby_profiles', sa.Column('gestational_age_at_birth_days', sa.Integer(), nullable=True))
    op.create_check_constraint(op.f('ck_infant_profiles_ck_infant_profiles_birth_weight_kg'), 'baby_profiles', 'birth_weight_kg IS NULL OR (birth_weight_kg >= 0.2 AND birth_weight_kg <= 10)')
    op.create_check_constraint(op.f('ck_infant_profiles_ck_infant_profiles_gestational_age_at_birth_days'), 'baby_profiles', 'gestational_age_at_birth_days IS NULL OR (gestational_age_at_birth_days >= 140 AND gestational_age_at_birth_days <= 315)')
    for name in ['sex', 'feeding_mode', 'version']:
        op.drop_constraint(op.f(f'ck_baby_profiles_{name}'), 'baby_profiles', type_='check')
    op.drop_column('baby_profiles', 'feeding_mode')
    op.drop_column('baby_profiles', 'version')
    op.alter_column('baby_profiles', 'sex', existing_type=sa.String(32), nullable=True)
    op.execute("UPDATE baby_profiles SET sex = NULL WHERE sex = 'unspecified'")
    op.alter_column('baby_profiles', 'sex', new_column_name='sex_at_birth')
    op.execute('ALTER INDEX ix_baby_profiles_birth_date RENAME TO ix_infant_profiles_birth_date')
    op.execute('ALTER INDEX ix_baby_profiles_owner_deleted_at RENAME TO ix_infant_profiles_owner_deleted_at')
    op.execute('ALTER TABLE baby_profiles RENAME CONSTRAINT pk_baby_profiles TO pk_infant_profiles')
    op.execute('ALTER TABLE baby_profiles RENAME CONSTRAINT fk_baby_profiles_owner_user_id_users TO fk_infant_profiles_owner_user_id_users')
    op.rename_table('baby_profiles', 'infant_profiles')
