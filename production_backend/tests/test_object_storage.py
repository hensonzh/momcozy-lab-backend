import asyncio

import pytest
from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.infrastructure.object_storage import LocalObjectStorage, S3ObjectStorage, create_object_storage


def test_local_object_storage_put_get_delete(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path)

    stored = asyncio.run(storage.put_bytes(key="uploads/example.txt", body=b"hello", content_type="text/plain"))

    assert stored.key == "uploads/example.txt"
    assert stored.uri == "local://uploads/example.txt"
    assert stored.size_bytes == 5
    assert asyncio.run(storage.get_bytes(key="uploads/example.txt")) == b"hello"

    asyncio.run(storage.delete(key="uploads/example.txt"))
    assert not (tmp_path / "uploads" / "example.txt").exists()


def test_local_object_storage_rejects_path_traversal(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(ValueError, match="relative"):
        asyncio.run(storage.put_bytes(key="../secret.txt", body=b"nope", content_type="text/plain"))


def test_create_object_storage_uses_local_provider(tmp_path) -> None:
    settings = Settings(app_env="test", object_storage_local_root=str(tmp_path))

    storage = create_object_storage(settings)

    assert isinstance(storage, LocalObjectStorage)


def test_create_object_storage_uses_s3_compatible_provider() -> None:
    settings = Settings(
        app_env="test",
        object_storage_provider="oss",
        object_storage_bucket="bucket",
        object_storage_endpoint_url="https://oss.example.test",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
    )

    storage = create_object_storage(settings)

    assert isinstance(storage, S3ObjectStorage)
    assert storage.bucket == "bucket"


def test_lifespan_registers_object_storage(tmp_path) -> None:
    app = create_app(Settings(app_env="test", object_storage_local_root=str(tmp_path)))

    with TestClient(app):
        assert isinstance(app.state.object_storage, LocalObjectStorage)


def test_lifespan_starts_with_production_s3_compatible_storage() -> None:
    app = create_app(
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://app:secret@postgres.internal:5432/momcozy",
            redis_url="redis://redis.internal:6379/0",
            object_storage_provider="s3",
            object_storage_bucket="bucket",
            object_storage_access_key_id="access",
            object_storage_secret_access_key="secret",
            auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
            trusted_hosts=("testserver",),
        )
    )

    with TestClient(app):
        assert isinstance(app.state.object_storage, S3ObjectStorage)
