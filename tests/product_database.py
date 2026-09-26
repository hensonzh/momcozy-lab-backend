"""Isolated database fixture for product records independent of retired services."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models  # noqa: F401 - register mapped tables
from app.modules.users.models import User

DATABASE_URL = os.getenv("MOMCOZY_TEST_DATABASE_URL", "")
postgres = pytest.mark.skipif(not DATABASE_URL, reason="Requires isolated MOMCOZY_TEST_DATABASE_URL PostgreSQL database.")


@asynccontextmanager
async def database():
    schema = f"product_records_{uuid4().hex}"
    admin = create_async_engine(DATABASE_URL)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
        owners = [uuid4(), uuid4()]
        async with sessions.begin() as session:
            session.add_all(User(id=owner) for owner in owners)
        yield sessions, None, now, None, owners, None
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
