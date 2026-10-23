"""Exercise the legacy record backfill against an isolated PostgreSQL schema."""

import asyncio
from importlib import import_module
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.modules.baby.models import BabyRecord
from app.modules.baby.profile_models import BabyProfile
from app.modules.records.models import FeedingRecord, GrowthRecord
from product_database import database, postgres


@postgres
def test_canonical_record_backfill_copies_valid_rows_once_and_keeps_legacy_sources():
    async def run():
        async with database() as (sessions, _, now, _, owners, _):
            engine = sessions.kw["bind"]
            baby_id, other_baby_id = uuid4(), uuid4()
            valid_feed_id, invalid_feed_id, deleted_feed_id, duplicate_feed_id = (
                uuid4() for _ in range(4)
            )
            valid_growth_id, invalid_growth_id, deleted_growth_id = (
                uuid4() for _ in range(3)
            )
            async with sessions.begin() as session:
                session.add_all(
                    [
                        BabyProfile(id=baby_id, owner_user_id=owners[0], name="Baby A"),
                        BabyProfile(id=other_baby_id, owner_user_id=owners[1], name="Baby B"),
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        FeedingRecord(
                            id=valid_feed_id,
                            owner_user_id=owners[0],
                            infant_id=baby_id,
                            feed_time=now,
                            feed_type="formula",
                            volume_ml=90,
                            title="legacy formula",
                        ),
                        FeedingRecord(
                            id=invalid_feed_id,
                            owner_user_id=owners[0],
                            infant_id=None,
                            feed_time=now,
                            feed_type="formula",
                            volume_ml=80,
                        ),
                        FeedingRecord(
                            id=deleted_feed_id,
                            owner_user_id=owners[0],
                            infant_id=baby_id,
                            feed_time=now,
                            feed_type="formula",
                            volume_ml=70,
                            status="deleted",
                            deleted_at=now,
                        ),
                        FeedingRecord(
                            id=duplicate_feed_id,
                            owner_user_id=owners[0],
                            infant_id=baby_id,
                            feed_time=now,
                            feed_type="formula",
                            volume_ml=60,
                        ),
                        BabyRecord(
                            id=uuid4(),
                            owner_user_id=owners[0],
                            baby_id=baby_id,
                            kind="feeding",
                            occurred_at=now,
                            data={"method": "formula", "volume_ml": 60, "note": ""},
                        ),
                        GrowthRecord(
                            id=valid_growth_id,
                            owner_user_id=owners[0],
                            infant_id=baby_id,
                            measured_at=now,
                            weight_kg=4.2,
                            height_cm=54,
                            head_cm=36,
                        ),
                        GrowthRecord(
                            id=invalid_growth_id,
                            owner_user_id=owners[0],
                            infant_id=None,
                            measured_at=now,
                            weight_kg=4.1,
                        ),
                        GrowthRecord(
                            id=deleted_growth_id,
                            owner_user_id=owners[0],
                            infant_id=baby_id,
                            measured_at=now,
                            weight_kg=4.0,
                            status="deleted",
                            deleted_at=now,
                        ),
                    ]
                )

            revision = import_module(
                "migrations.versions.20261023_0036_canonical_record_backfill"
            )

            def upgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    revision.upgrade()

            async with engine.begin() as connection:
                await connection.run_sync(upgrade)
                # Stable IDs and NOT EXISTS guards make a retried backfill idempotent.
                await connection.run_sync(upgrade)

            async with sessions.begin() as session:
                records = (
                    await session.scalars(
                        select(BabyRecord).where(BabyRecord.owner_user_id == owners[0])
                    )
                ).all()
                feedings = [record for record in records if record.kind == "feeding"]
                growth = [record for record in records if record.kind == "growth"]
                assert len(feedings) == 2
                migrated_feed = next(
                    record for record in feedings if record.data.get("volume_ml") == 90
                )
                assert migrated_feed.baby_id == baby_id
                assert migrated_feed.data == {
                    "method": "formula",
                    "volume_ml": 90,
                    "note": "legacy formula",
                }
                assert {(record.data["metric"], record.data["value"]) for record in growth} == {
                    ("weight", 4.2),
                    ("length", 54.0),
                    ("head_circumference", 36.0),
                }
                assert all(record.data["timezone"] == "UTC" for record in growth)
                assert await session.scalar(select(func.count()).select_from(FeedingRecord)) == 4
                assert await session.scalar(select(func.count()).select_from(GrowthRecord)) == 3
                assert not any(record.owner_user_id == owners[1] for record in records)

    asyncio.run(run())
