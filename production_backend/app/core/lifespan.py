from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .settings import Settings
from ..infrastructure.db.session import create_db_engine, create_session_factory


def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_engine = create_db_engine(settings)
        app.state.db_engine = db_engine
        app.state.db_session_factory = create_session_factory(db_engine)
        try:
            yield
        finally:
            await db_engine.dispose()

    return lifespan
