"""Backfill legacy infant feeding and growth rows into canonical baby records.

Revision ID: 20261023_0027
Revises: 20261015_0026
"""

from alembic import op
import sqlalchemy as sa

revision = "20261023_0027"
down_revision = "20261015_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep legacy tables readable for compatibility. Canonical rows use stable,
    # source-derived UUIDs and NOT EXISTS guards so a retried migration cannot
    # duplicate records that were already copied.
    op.execute(
        sa.text(
            """
            INSERT INTO baby_records (
                id, owner_user_id, baby_id, kind, occurred_at, recorded_on,
                ended_at, data, version, deleted_at, created_at, updated_at
            )
            SELECT
                (md5(feeding.id::text || '-feeding'))::uuid,
                feeding.owner_user_id,
                feeding.infant_id,
                'feeding',
                feeding.feed_time,
                NULL,
                NULL,
                jsonb_strip_nulls(jsonb_build_object(
                    'method', CASE feeding.feed_type
                        WHEN 'breastfeeding' THEN 'breastfeeding'
                        WHEN 'direct_breastfeeding' THEN 'breastfeeding'
                        WHEN 'expressed_milk' THEN 'expressed_milk'
                        WHEN 'formula' THEN 'formula'
                    END,
                    'side', CASE
                        WHEN feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding')
                             AND feeding.feed_action IN ('left', 'right', 'both')
                        THEN feeding.feed_action
                    END,
                    'volume_ml', CASE
                        WHEN feeding.feed_type IN ('expressed_milk', 'formula')
                        THEN feeding.volume_ml
                    END,
                    'duration_minutes', CASE
                        WHEN feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding')
                             AND feeding.duration_seconds IS NOT NULL
                        THEN GREATEST(1, CEIL(feeding.duration_seconds / 60.0)::integer)
                    END,
                    'note', COALESCE(feeding.title, '')
                )),
                1,
                NULL,
                feeding.created_at,
                feeding.updated_at
            FROM feeding_records feeding
            WHERE feeding.infant_id IS NOT NULL
              AND feeding.status = 'active'
              AND feeding.deleted_at IS NULL
              AND feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding', 'expressed_milk', 'formula')
              AND (
                    (feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding')
                     AND feeding.feed_action IN ('left', 'right', 'both'))
                 OR (feeding.feed_type IN ('expressed_milk', 'formula')
                     AND feeding.volume_ml > 0)
              )
              AND NOT EXISTS (
                  SELECT 1 FROM baby_records current
                  WHERE current.owner_user_id = feeding.owner_user_id
                    AND current.baby_id = feeding.infant_id
                    AND current.kind = 'feeding'
                    AND current.occurred_at = feeding.feed_time
                    AND current.deleted_at IS NULL
                    AND current.data->>'method' = CASE feeding.feed_type
                        WHEN 'breastfeeding' THEN 'breastfeeding'
                        WHEN 'direct_breastfeeding' THEN 'breastfeeding'
                        WHEN 'expressed_milk' THEN 'expressed_milk'
                        WHEN 'formula' THEN 'formula'
                    END
                    AND current.data->>'side' IS NOT DISTINCT FROM CASE
                        WHEN feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding')
                             AND feeding.feed_action IN ('left', 'right', 'both')
                        THEN feeding.feed_action
                    END
                    AND (current.data->>'volume_ml')::double precision
                        IS NOT DISTINCT FROM CASE
                            WHEN feeding.feed_type IN ('expressed_milk', 'formula')
                            THEN feeding.volume_ml
                        END
                    AND (current.data->>'duration_minutes')::integer
                        IS NOT DISTINCT FROM CASE
                            WHEN feeding.feed_type IN ('breastfeeding', 'direct_breastfeeding')
                                 AND feeding.duration_seconds IS NOT NULL
                            THEN GREATEST(1, CEIL(feeding.duration_seconds / 60.0)::integer)
                        END
                    AND COALESCE(current.data->>'note', '') = COALESCE(feeding.title, '')
              )
            """
        )
    )

    for metric, column, unit_limit in (
        ("weight", "weight_kg", 50),
        ("length", "height_cm", 150),
        ("head_circumference", "head_cm", 150),
    ):
        op.execute(
            sa.text(
                f"""
                INSERT INTO baby_records (
                    id, owner_user_id, baby_id, kind, occurred_at, recorded_on,
                    ended_at, data, version, deleted_at, created_at, updated_at
                )
                SELECT
                    (md5(growth.id::text || '-{metric}'))::uuid,
                    growth.owner_user_id,
                    growth.infant_id,
                    'growth',
                    NULL,
                    (growth.measured_at AT TIME ZONE 'UTC')::date,
                    NULL,
                    jsonb_build_object(
                        'timezone', 'UTC',
                        'metric', '{metric}',
                        'value', growth.{column}
                    ),
                    1,
                    NULL,
                    growth.created_at,
                    growth.updated_at
                FROM growth_records growth
                WHERE growth.infant_id IS NOT NULL
                  AND growth.status = 'active'
                  AND growth.deleted_at IS NULL
                  AND growth.{column} > 0
                  AND growth.{column} <= {unit_limit}
                  AND NOT EXISTS (
                      SELECT 1 FROM baby_records current
                      WHERE current.owner_user_id = growth.owner_user_id
                        AND current.baby_id = growth.infant_id
                        AND current.kind = 'growth'
                        AND current.recorded_on = (growth.measured_at AT TIME ZONE 'UTC')::date
                        AND current.deleted_at IS NULL
                        AND current.data->>'metric' = '{metric}'
                        AND (current.data->>'value')::double precision = growth.{column}
                  )
                """
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Canonical record backfill is intentionally irreversible; legacy source rows remain available."
    )
