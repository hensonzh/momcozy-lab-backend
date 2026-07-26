"""harden maternal and infant profile invariants

Revision ID: 20260726_0049
Revises: 20260726_0048
Create Date: 2026-07-26 18:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260726_0049"
down_revision = "20260726_0048"
branch_labels = None
depends_on = None


_SINGLE_INFANT_CANDIDATES = """
    SELECT
        infants.owner_user_id,
        min(infants.id::text)::uuid AS infant_id
    FROM infant_profiles AS infants
    WHERE infants.deleted_at IS NULL
    GROUP BY infants.owner_user_id
    HAVING count(*) = 1
"""


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO maternal_profiles (id, owner_user_id)
        SELECT
            md5(single_infants.owner_user_id::text || '-maternal-profile')::uuid,
            single_infants.owner_user_id
        FROM ({_SINGLE_INFANT_CANDIDATES}) AS single_infants
        JOIN infant_profiles AS infant
          ON infant.id = single_infants.infant_id
        LEFT JOIN user_profiles AS user_profile
          ON user_profile.user_id = single_infants.owner_user_id
        LEFT JOIN maternal_profiles AS maternal
          ON maternal.owner_user_id = single_infants.owner_user_id
        WHERE maternal.id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM maternal_current_delivery_infants AS current_link
              JOIN maternal_profiles AS linked_maternal
                ON linked_maternal.id = current_link.maternal_profile_id
              WHERE linked_maternal.owner_user_id =
                    single_infants.owner_user_id
          )
          AND (
              user_profile.estimated_due_date IS NULL
              OR infant.birth_date IS NULL
              OR abs(user_profile.estimated_due_date - infant.birth_date) <= 42
          )
        ON CONFLICT (owner_user_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO maternal_current_delivery_infants (
            maternal_profile_id,
            infant_id,
            birth_order
        )
        SELECT maternal.id, single_infants.infant_id, 1
        FROM ({_SINGLE_INFANT_CANDIDATES}) AS single_infants
        JOIN infant_profiles AS infant
          ON infant.id = single_infants.infant_id
        JOIN maternal_profiles AS maternal
          ON maternal.owner_user_id = single_infants.owner_user_id
        LEFT JOIN user_profiles AS user_profile
          ON user_profile.user_id = single_infants.owner_user_id
        WHERE NOT EXISTS (
              SELECT 1
              FROM maternal_current_delivery_infants AS current_link
              JOIN maternal_profiles AS linked_maternal
                ON linked_maternal.id = current_link.maternal_profile_id
              WHERE linked_maternal.owner_user_id =
                    single_infants.owner_user_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM maternal_current_delivery_infants AS infant_link
              WHERE infant_link.infant_id = single_infants.infant_id
          )
          AND (
              maternal.latest_delivery_date IS NOT NULL
              AND (
                  infant.birth_date IS NULL
                  OR maternal.latest_delivery_date = infant.birth_date
              )
              OR user_profile.estimated_due_date IS NULL
              OR infant.birth_date IS NULL
              OR abs(user_profile.estimated_due_date - infant.birth_date) <= 42
          )
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        UPDATE maternal_profiles
        SET has_cesarean_history = TRUE
        WHERE latest_delivery_method = 'cesarean'
          AND has_cesarean_history IS NOT TRUE
        """
    )
    op.create_check_constraint(
        "ck_maternal_profiles_cesarean_history",
        "maternal_profiles",
        "latest_delivery_method <> 'cesarean' "
        "OR has_cesarean_history IS TRUE",
    )

    op.execute(
        """
        UPDATE user_profiles AS user_profile
        SET estimated_due_date = NULL
        WHERE user_profile.estimated_due_date IS NOT NULL
          AND (
              EXISTS (
                  SELECT 1
                  FROM maternal_profiles AS maternal
                  WHERE maternal.owner_user_id = user_profile.user_id
                    AND maternal.latest_delivery_date IS NOT NULL
              )
              OR EXISTS (
                  SELECT 1
                  FROM maternal_profiles AS maternal
                  JOIN maternal_current_delivery_infants AS current_link
                    ON current_link.maternal_profile_id = maternal.id
                  JOIN infant_profiles AS infant
                    ON infant.id = current_link.infant_id
                  WHERE maternal.owner_user_id = user_profile.user_id
                    AND infant.owner_user_id = user_profile.user_id
                    AND infant.deleted_at IS NULL
                    AND infant.birth_date IS NOT NULL
              )
          )
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_maternal_profiles_cesarean_history",
        "maternal_profiles",
        type_="check",
    )
