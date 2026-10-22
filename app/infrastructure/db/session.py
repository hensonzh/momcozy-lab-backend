from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ...core.settings import Settings

LOGGER = logging.getLogger("production_backend.db.session")
AFTER_COMMIT_CALLBACKS_KEY = "after_commit_callbacks"
AFTER_ROLLBACK_CALLBACKS_KEY = "after_rollback_callbacks"
AfterCommitCallback = Callable[[], Awaitable[None]]


def create_db_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
            session.info.pop(AFTER_ROLLBACK_CALLBACKS_KEY, None)
            await run_after_commit_callbacks(session)
        except BaseException:
            await session.rollback()
            await run_after_rollback_callbacks(session)
            raise


def add_after_commit_callback(session: AsyncSession, callback: AfterCommitCallback) -> None:
    callbacks = session.info.setdefault(AFTER_COMMIT_CALLBACKS_KEY, [])
    callbacks.append(callback)


async def run_after_commit_callbacks(session: AsyncSession) -> None:
    callbacks = list(session.info.pop(AFTER_COMMIT_CALLBACKS_KEY, []))
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            LOGGER.warning("Post-commit callback failed.", exc_info=True)


def add_after_rollback_callback(session: AsyncSession, callback: AfterCommitCallback) -> None:
    callbacks = session.info.setdefault(AFTER_ROLLBACK_CALLBACKS_KEY, [])
    callbacks.append(callback)


async def run_after_rollback_callbacks(session: AsyncSession) -> None:
    callbacks = list(session.info.pop(AFTER_ROLLBACK_CALLBACKS_KEY, []))
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            LOGGER.warning("Post-rollback cleanup failed; manual orphan reconciliation required.")
