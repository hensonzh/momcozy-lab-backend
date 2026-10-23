"""The result migration adds payloads without introducing another task database."""
import asyncio
from importlib import import_module
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from product_database import DATABASE_URL, postgres


@postgres
def test_result_migration_preserves_progress_and_accepts_validated_result_events():
    async def run():
        schema = f"stool_results_{uuid4().hex}"
        admin = create_async_engine(DATABASE_URL)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
        migration = import_module("migrations.versions.20261023_0037_stool_analysis_results")
        try:
            async with engine.begin() as connection:
                await connection.execute(text("""CREATE TABLE stool_analysis_events (
                    id uuid PRIMARY KEY, event_type varchar(48) NOT NULL,
                    CONSTRAINT ck_stool_analysis_events_nonmedical_type CHECK
                    (event_type IN ('stool.card.created','stool.analysis.started','stool.analysis.failed','stool.analysis.cancelled'))
                )"""))
                await connection.execute(text(
                    "INSERT INTO stool_analysis_events (id,event_type) VALUES (:id,'stool.analysis.started')"
                ), {"id": uuid4()})

                def upgrade(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        migration.upgrade()
                await connection.run_sync(upgrade)
                await connection.execute(text("""INSERT INTO stool_analysis_events (id,event_type,payload)
                    VALUES (:field,'stool.field.ready','{"field":"color","value":"yellow"}'::jsonb),
                           (:done,'stool.analysis.completed','{"result":{"schema_version":"stool.result.v1"}}'::jsonb)"""),
                    {"field": uuid4(), "done": uuid4()})
                assert (await connection.execute(text(
                    "SELECT count(*) FROM stool_analysis_events WHERE payload IS NOT NULL"
                ))).scalar_one() == 2
                assert (await connection.execute(text(
                    "SELECT to_regclass('stool_specialty_jobs') IS NULL"
                ))).scalar_one()
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
