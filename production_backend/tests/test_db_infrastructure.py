import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory


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


async def _assert_session_factory_creates_async_session() -> None:
    settings = Settings(app_env="test")
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            assert isinstance(session, AsyncSession)
    finally:
        await engine.dispose()
