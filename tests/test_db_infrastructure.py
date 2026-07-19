import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.db.session import (
    add_after_commit_callback,
    create_db_engine,
    create_session_factory,
    run_after_commit_callbacks,
)


def test_create_db_engine_and_session_factory() -> None:
    settings = Settings(app_env="test")
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        assert isinstance(engine, AsyncEngine)
        assert isinstance(session_factory, async_sessionmaker)
    finally:
        asyncio.run(engine.dispose())


def test_lifespan_registers_db_handles() -> None:
    app = create_app(Settings(app_env="test"))

    with TestClient(app):
        assert isinstance(app.state.db_engine, AsyncEngine)
        assert isinstance(app.state.db_session_factory, async_sessionmaker)


def test_session_factory_creates_async_session() -> None:
    asyncio.run(_assert_session_factory_creates_async_session())


def test_after_commit_callbacks_run_after_successful_commit() -> None:
    asyncio.run(_assert_after_commit_callbacks_run_after_successful_commit())


async def _assert_session_factory_creates_async_session() -> None:
    settings = Settings(app_env="test")
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            assert isinstance(session, AsyncSession)
    finally:
        await engine.dispose()


async def _assert_after_commit_callbacks_run_after_successful_commit() -> None:
    settings = Settings(app_env="test")
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    calls = []

    async def callback() -> None:
        calls.append("notified")

    try:
        async with session_factory() as session:
            add_after_commit_callback(session, callback)
            await session.commit()
            await run_after_commit_callbacks(session)
    finally:
        await engine.dispose()

    assert calls == ["notified"]
