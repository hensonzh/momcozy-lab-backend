"""canonicalize legacy invite identity subjects

Revision ID: 20260720_0038
Revises: 20260720_0037
Create Date: 2026-07-20 10:30:00
"""

from __future__ import annotations

from alembic import op


revision = "20260720_0038"
down_revision = "20260720_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older releases stored invite identities as CODE:DEVICE. The current
    # contract stores CODE in subject and DEVICE in device_id. When duplicates
    # exist, only the earliest identity is canonicalized; the others are kept
    # untouched so the migration never deletes user-owned data.
    op.execute(
        """
        WITH legacy_invites AS (
            SELECT
                identity.id,
                split_part(identity.subject, ':', 1) AS invite_code,
                row_number() OVER (
                    PARTITION BY split_part(identity.subject, ':', 1)
                    ORDER BY identity.created_at ASC, identity.id ASC
                ) AS row_number
            FROM auth_identities AS identity
            WHERE identity.provider = 'invite'
              AND position(':' in identity.subject) > 0
        )
        UPDATE auth_identities AS identity
        SET subject = legacy.invite_code
        FROM legacy_invites AS legacy
        WHERE identity.id = legacy.id
          AND legacy.row_number = 1
          AND NOT EXISTS (
              SELECT 1
              FROM auth_identities AS canonical
              WHERE canonical.provider = 'invite'
                AND canonical.subject = legacy.invite_code
          )
        """
    )


def downgrade() -> None:
    # device_id remains the authority for binding. Reconstructing a historical
    # CODE:DEVICE subject would reintroduce the retired contract and may collide
    # with identities created after this migration, so rollback is intentionally
    # data-preserving.
    pass
