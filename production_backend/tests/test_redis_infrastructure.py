import asyncio

from fastapi.testclient import TestClient
from redis.asyncio import Redis

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.infrastructure.redis.client import create_redis_client


def test_create_redis_client_from_settings() -> None:
    client = create_redis_client(Settings(app_env="test", redis_url="redis://localhost:6379/7"))

    try:
        assert isinstance(client, Redis)
        assert client.connection_pool.connection_kwargs["db"] == 7
    finally:
        asyncio.run(client.aclose())


def test_lifespan_registers_redis_client() -> None:
    app = create_app(Settings(app_env="test"))

    with TestClient(app):
        assert isinstance(app.state.redis_client, Redis)
