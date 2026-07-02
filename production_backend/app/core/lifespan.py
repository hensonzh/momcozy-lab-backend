from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .settings import Settings
from ..infrastructure.db.session import create_db_engine, create_session_factory
from ..infrastructure.redis.client import close_redis_client, create_redis_client


def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_engine = create_db_engine(settings)
        redis_client = create_redis_client(settings)
        app.state.db_engine = db_engine
        app.state.db_session_factory = create_session_factory(db_engine)
        app.state.redis_client = redis_client
        try:
            yield
        finally:
            await close_redis_client(redis_client)
            await db_engine.dispose()

    return lifespan
