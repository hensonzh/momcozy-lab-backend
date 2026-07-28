import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.object_storage import LocalObjectStorage, S3ObjectStorage, create_object_storage
from tests.auth_key_material import TEST_RSA_PRIVATE_KEY_B64


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"
AGENT_RUNTIME_SERVICE_KEY = "agent-runtime-service-key-with-at-least-32-bytes"


def test_local_object_storage_put_get_delete(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path)

    stored = asyncio.run(storage.put_bytes(key="uploads/example.txt", body=b"hello", content_type="text/plain"))

    assert stored.key == "uploads/example.txt"
    assert stored.uri == "local://uploads/example.txt"
    assert stored.size_bytes == 5
    assert asyncio.run(storage.get_bytes(key="uploads/example.txt")) == b"hello"
    assert asyncio.run(storage.get_byte_range(key="uploads/example.txt", start=1, end=3)) == b"ell"

    asyncio.run(storage.delete(key="uploads/example.txt"))
    assert not (tmp_path / "uploads" / "example.txt").exists()


def test_s3_object_storage_requests_only_the_selected_byte_range() -> None:
    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage.client = _FakeS3Client(body=b"ell")

    body = asyncio.run(storage.get_byte_range(key="uploads/example.txt", start=1, end=3))

    assert body == b"ell"
    assert storage.client.calls == [
        {
            "Bucket": "bucket",
            "Key": "uploads/example.txt",
            "Range": "bytes=1-3",
        },
    ]


def test_s3_object_storage_creates_https_presigned_get_url() -> None:
    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage.public_client = _FakeS3Client(body=b"")

    image_url = asyncio.run(
        storage.create_presigned_get_url(
            key="users/user/files/image.png",
            expires_in_seconds=604800,
        )
    )

    assert image_url == "https://images.example.test/signed"
    assert storage.public_client.presign_calls == [
        {
            "ClientMethod": "get_object",
            "Params": {"Bucket": "bucket", "Key": "users/user/files/image.png"},
            "ExpiresIn": 604800,
        }
    ]


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


def test_create_object_storage_rejects_unsupported_provider() -> None:
    settings = Settings(app_env="test", object_storage_provider="ftp")

    with pytest.raises(ValueError, match="unsupported object storage provider"):
        create_object_storage(settings)


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
            auth_jwt_private_key_b64=TEST_RSA_PRIVATE_KEY_B64,
            auth_jwt_issuer="momcozy-test",
            auth_jwt_product_audience="momcozy-product-api",
            auth_jwt_runtime_audience="momcozy-agent-runtime",
                service_api_key=SERVICE_KEY,
                agent_runtime_service_api_key=AGENT_RUNTIME_SERVICE_KEY,
                agent_model_asset_public_base_url="https://api.example.test",
                trusted_hosts=("testserver",),
        )
    )

    with TestClient(app):
        assert isinstance(app.state.object_storage, S3ObjectStorage)


class _FakeS3Client:
    def __init__(self, *, body: bytes) -> None:
        self.body = body
        self.calls: list[dict[str, str]] = []
        self.presign_calls: list[dict[str, object]] = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"Body": _FakeS3Body(self.body)}

    def generate_presigned_url(self, **kwargs):
        self.presign_calls.append(kwargs)
        return "https://images.example.test/signed"


class _FakeS3Body:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body
