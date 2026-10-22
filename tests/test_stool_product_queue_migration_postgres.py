"""One Product migration adds the local task queue; no second-service tables."""
import asyncio
from importlib import import_module
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from product_database import DATABASE_URL, postgres


@postgres
def test_direct_product_queue_migration_quarantines_legacy_tasks_without_remote_outbox():
    async def run():
        schema = f"stool_product_queue_{uuid4().hex}"
        admin = create_async_engine(DATABASE_URL)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
        migration = import_module("migrations.versions.20261022_0034_stool_product_queue")
        try:
            async with engine.begin() as conn:
                await conn.execute(text("""CREATE TABLE stool_analyses (
                    id uuid PRIMARY KEY, status varchar(24) NOT NULL,
                    deleted_at timestamptz, revoked_at timestamptz, created_at timestamptz DEFAULT now()
                )"""))
                await conn.execute(text("""CREATE TABLE stool_analysis_events (
                    id uuid PRIMARY KEY, analysis_id uuid REFERENCES stool_analyses(id),
                    sequence int NOT NULL, schema_version varchar(40), event_type varchar(48),
                    CONSTRAINT ck_stool_analysis_events_nonmedical_type CHECK
                    (event_type IN ('stool.card.created','stool.analysis.failed','stool.analysis.cancelled'))
                )"""))
                old_id, erased_id = uuid4(), uuid4()
                await conn.execute(text("INSERT INTO stool_analyses (id,status) VALUES (:id,'queued'),(:erased,'queued')"),
                                   {"id": old_id, "erased": erased_id})
                await conn.execute(text("UPDATE stool_analyses SET deleted_at=now() WHERE id=:id"), {"id": erased_id})
                await conn.execute(text("""INSERT INTO stool_analysis_events (id,analysis_id,sequence,schema_version,event_type)
                    VALUES (:event_id,:analysis_id,1,'stool.events.v1','stool.card.created')"""),
                                   {"event_id": uuid4(), "analysis_id": old_id})
                def upgrade(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        migration.upgrade()
                await conn.run_sync(upgrade)
                assert (await conn.execute(text("SELECT status,run_attempt,market FROM stool_analyses WHERE id=:id"),
                                           {"id": old_id})).one() == ("failed", 0, None)
                assert (await conn.execute(text("SELECT status FROM stool_analyses WHERE id=:id"),
                                           {"id": erased_id})).scalar_one() == "failed"
                assert (await conn.execute(text("SELECT sequence,event_type FROM stool_analysis_events WHERE analysis_id=:id ORDER BY sequence"),
                                           {"id": old_id})).all() == [(1, "stool.card.created"), (2, "stool.analysis.failed")]
                assert (await conn.execute(text("SELECT to_regclass('stool_analysis_dispatch_intents') IS NULL"))).scalar_one()
                assert (await conn.execute(text("SELECT to_regclass('stool_specialty_jobs') IS NULL"))).scalar_one()
                await conn.execute(text("""INSERT INTO stool_analysis_events (id,analysis_id,sequence,schema_version,event_type)
                    VALUES (:event_id,:analysis_id,3,'stool.events.v1','stool.analysis.started')"""),
                    {"event_id": uuid4(), "analysis_id": old_id})
                await conn.execute(text("""UPDATE stool_analyses SET
                    baby_context_snapshot_id=:snapshot,market='US',language='en',baby_profile_version=1,
                    baby_feeding_mode='unknown',context_captured_at=now() WHERE id=:id"""),
                                   {"id": old_id, "snapshot": uuid4()})
                assert (await conn.execute(text("SELECT baby_context_snapshot_id IS NOT NULL FROM stool_analyses WHERE id=:id"),
                                           {"id": old_id})).scalar_one()
        finally:
            await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
