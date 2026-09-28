"""Keep one current daily status per baby and date; remove duplicate snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "20260928_0025"
down_revision = "20260927_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Block concurrent writes until the deduplication and unique index commit.
    op.execute(sa.text("LOCK TABLE baby_records IN SHARE ROW EXCLUSIVE MODE"))
    # A repeated count is a replacement, not an increment. Preserve the last
    # recorded non-null value of each field, then delete the obsolete rows.
    # Soft-deleted records are not current and are intentionally left alone.
    columns = ("mental_state", "wet_count", "stool_count", "color", "consistency", "timezone")
    latest = ",\n".join(
        f"""(SELECT source.data->'{column}' FROM baby_records source
            WHERE source.owner_user_id = winner.owner_user_id
              AND source.baby_id = winner.baby_id
              AND source.recorded_on = winner.recorded_on
              AND source.kind = 'daily_status' AND source.deleted_at IS NULL
              AND source.data->>'{column}' IS NOT NULL
            ORDER BY source.updated_at DESC, source.created_at DESC, source.id DESC
            LIMIT 1) AS {column}"""
        for column in columns
    )
    op.execute(sa.text(f"""
        CREATE TEMP TABLE daily_status_canonical ON COMMIT DROP AS
        WITH ranked AS (
            SELECT id, owner_user_id, baby_id, recorded_on,
                   row_number() OVER day_order AS position,
                   count(*) OVER day_group AS total
            FROM baby_records
            WHERE kind = 'daily_status' AND deleted_at IS NULL
            WINDOW day_group AS (PARTITION BY owner_user_id, baby_id, recorded_on),
                   day_order AS (PARTITION BY owner_user_id, baby_id, recorded_on
                                   ORDER BY updated_at DESC, created_at DESC, id DESC)
        )
        SELECT winner.id AS keeper_id, winner.owner_user_id, winner.baby_id, winner.recorded_on,
               {latest}
        FROM ranked winner
        WHERE winner.position = 1 AND winner.total > 1
    """))
    # Keep existing replay receipts pointing at a live row after obsolete
    # snapshots are removed. Audit entries are historical and stay unchanged.
    op.execute(sa.text("""
        CREATE TEMP TABLE daily_status_replaced ON COMMIT DROP AS
        SELECT old.id AS old_id, canonical.keeper_id, canonical.owner_user_id, canonical.baby_id
        FROM baby_records old JOIN daily_status_canonical canonical
          ON old.owner_user_id = canonical.owner_user_id
         AND old.baby_id = canonical.baby_id
         AND old.recorded_on = canonical.recorded_on
        WHERE old.kind = 'daily_status' AND old.deleted_at IS NULL
          AND old.id <> canonical.keeper_id
    """))
    data = ", ".join(f"'{column}', canonical.{column}" for column in columns)
    op.execute(sa.text(f"""
        UPDATE baby_records current
        SET data = current.data || jsonb_strip_nulls(jsonb_build_object({data})),
            version = current.version + 1, updated_at = now()
        FROM daily_status_canonical canonical
        WHERE current.id = canonical.keeper_id
    """))
    op.execute(sa.text("""
        UPDATE idempotency_keys key
        SET response_ref = replaced.keeper_id::text
        FROM daily_status_replaced replaced
        WHERE key.actor_user_id = replaced.owner_user_id
          AND key.scope = 'baby_record.create:' || replaced.baby_id::text
          AND key.response_ref = replaced.old_id::text
    """))
    op.execute(sa.text("""
        UPDATE agent_batch_receipts receipt
        SET result = jsonb_set(receipt.result, '{items}', (
            SELECT jsonb_agg(
                CASE WHEN replaced.old_id IS NULL THEN entry.value
                     ELSE jsonb_set(
                         jsonb_set(entry.value, '{resource_id}', to_jsonb(replaced.keeper_id::text)),
                         '{revision}', to_jsonb(current.version::text))
                END ORDER BY entry.ordinality)
            FROM jsonb_array_elements(receipt.result->'items') WITH ORDINALITY AS entry(value, ordinality)
            LEFT JOIN daily_status_replaced replaced
              ON replaced.old_id::text = entry.value->>'resource_id'
             AND replaced.owner_user_id = receipt.owner_user_id
            LEFT JOIN baby_records current ON current.id = replaced.keeper_id
        ))
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(receipt.result->'items') AS entry
            JOIN daily_status_replaced replaced
              ON replaced.old_id::text = entry->>'resource_id'
             AND replaced.owner_user_id = receipt.owner_user_id
        )
    """))
    op.execute(sa.text("""
        DELETE FROM baby_records old USING daily_status_canonical canonical
        WHERE old.owner_user_id = canonical.owner_user_id
          AND old.baby_id = canonical.baby_id
          AND old.recorded_on = canonical.recorded_on
          AND old.kind = 'daily_status' AND old.deleted_at IS NULL
          AND old.id <> canonical.keeper_id
    """))
    op.create_index(
        "uq_baby_records_active_daily_status", "baby_records", ["baby_id", "recorded_on"],
        unique=True, postgresql_where=sa.text("kind = 'daily_status' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_baby_records_active_daily_status", table_name="baby_records")
