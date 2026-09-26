"""Exercise the prior-history migration against an isolated legacy table."""

import asyncio
from importlib import import_module
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from product_database import DATABASE_URL, postgres


@postgres
def test_legacy_cesarean_history_is_not_mistaken_for_a_prior_birth():
    async def run():
        schema = f"onboarding_migration_{uuid4().hex}"
        admin = create_async_engine(DATABASE_URL)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            DATABASE_URL, connect_args={"server_settings": {"search_path": schema}}
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(
                    "CREATE TABLE maternal_profiles (id integer PRIMARY KEY, delivery_count integer, "
                    "latest_delivery_method text, has_cesarean_history boolean, "
                    "CONSTRAINT ck_maternal_profiles_cesarean_history "
                    "CHECK (latest_delivery_method <> 'cesarean' OR has_cesarean_history IS TRUE))"
                ))
                await connection.execute(text(
                    "INSERT INTO maternal_profiles VALUES "
                    "(1, 1, 'cesarean', TRUE), "
                    "(2, 2, 'cesarean', TRUE), "
                    "(3, 2, 'vaginal', TRUE), "
                    "(4, NULL, 'cesarean', TRUE)"
                ))

                def upgrade(sync_connection):
                    with Operations.context(MigrationContext.configure(sync_connection)):
                        import_module("migrations.versions.20260926_0022_prior_cesarean_history").upgrade()

                await connection.run_sync(upgrade)
                rows = (await connection.execute(text(
                    "SELECT id, has_cesarean_history FROM maternal_profiles ORDER BY id"
                ))).all()
                assert rows == [(1, False), (2, None), (3, True), (4, None)]
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
