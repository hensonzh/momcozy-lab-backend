from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.files.agent_router import get_agent_file_service
from app.modules.files.agent_service import AgentFileAccessService, AgentModelBytesService
from app.modules.files.agent_asset_capability import IssuedAgentAssetCapability


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_file_resolve_requires_runtime_service_identity() -> None:
    response = TestClient(_app()).post(
        "/v1/internal/agent/files/resolve",
        json={
            "actor_user_id": str(uuid4()),
            "file_id": str(uuid4()),
            "purpose": "model_image",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_file_resolve_uses_explicit_actor_scope_and_returns_no_object_key() -> None:
    actor_user_id = uuid4()
    file_id = uuid4()
    service = FakeAgentFileAccessService(file_id=file_id)
    app = _app()
    app.dependency_overrides[get_agent_file_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/files/resolve",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "X-Request-ID": "req-file-resolve",
        },
        json={
            "actor_user_id": str(actor_user_id),
            "file_id": str(file_id),
            "purpose": "model_image",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "file_id": str(file_id),
        "content_type": "image/png",
        "original_filename": "checkup.png",
        "model_url": "https://api.example.test/v1/model-assets/opaque-capability",
        "expires_at": "2026-07-27T00:00:00Z",
    }
    assert "object_key" not in response.json()
    assert service.kwargs == {
        "owner_user_id": actor_user_id,
        "file_id": file_id,
        "purpose": "model_image",
    }


def test_agent_file_access_rejects_cross_owner_or_deleted_file() -> None:
    service = AgentFileAccessService(
        repository=FakeFileRepository(None),
        capability_store=FakeCapabilityStore(),
        public_base_url="https://api.example.test",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.resolve(
                owner_user_id=uuid4(),
                file_id=uuid4(),
                purpose="model_image",
            )
        )

    assert exc_info.value.code == "invalid_agent_attachment"


@pytest.mark.parametrize(
    ("purpose", "content_type"),
    (
        ("model_image", "application/pdf"),
        ("model_file", "image/png"),
        ("model_file", "text/plain"),
    ),
)
def test_agent_file_access_rejects_content_type_outside_purpose(
    purpose: str,
    content_type: str,
) -> None:
    file_object = _file_object(content_type=content_type)
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        capability_store=FakeCapabilityStore(),
        public_base_url="https://api.example.test",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.resolve(
                owner_user_id=file_object.owner_user_id,
                file_id=file_object.id,
                purpose=purpose,  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.code == "invalid_agent_attachment"


def test_agent_file_access_returns_stable_opaque_product_url_and_expiry() -> None:
    file_object = _file_object(content_type="image/webp")
    capability_store = FakeCapabilityStore()
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        capability_store=capability_store,
        public_base_url="https://api.example.test",
    )

    result = asyncio.run(
        service.resolve(
            owner_user_id=file_object.owner_user_id,
            file_id=file_object.id,
            purpose="model_image",
        )
    )

    assert result.file_id == file_object.id
    assert result.model_url == "https://api.example.test/v1/model-assets/opaque-capability"
    assert result.expires_at == datetime(2026, 7, 26, 0, 30, tzinfo=timezone.utc)
    assert capability_store.capabilities[0].owner_user_id == file_object.owner_user_id
    assert capability_store.capabilities[0].file_id == file_object.id
    assert capability_store.capabilities[0].object_key == file_object.object_key
    assert capability_store.capabilities[0].purpose == "model_image"
    assert capability_store.capabilities[0].content_type == "image/webp"


def test_agent_file_access_reuses_exact_url_but_rechecks_file_authorization() -> None:
    file_object = _file_object(content_type="image/png")
    capability_store = FakeCapabilityStore()
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        capability_store=capability_store,
        public_base_url="https://api.example.test",
    )

    first = asyncio.run(
        service.resolve(
            owner_user_id=file_object.owner_user_id,
            file_id=file_object.id,
            purpose="model_image",
        )
    )
    second = asyncio.run(
        service.resolve(
            owner_user_id=file_object.owner_user_id,
            file_id=file_object.id,
            purpose="model_image",
        )
    )
    file_object.status = "deleted"

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.resolve(
                owner_user_id=file_object.owner_user_id,
                file_id=file_object.id,
                purpose="model_image",
            )
        )

    assert first.model_url == second.model_url
    assert first.expires_at == second.expires_at
    assert len(capability_store.capabilities) == 2
    assert exc_info.value.code == "invalid_agent_attachment"


def test_agent_file_access_fails_closed_for_non_https_public_base_url() -> None:
    file_object = _file_object(content_type="image/png")
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        capability_store=FakeCapabilityStore(),
        public_base_url="http://api.internal",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.resolve(
                owner_user_id=file_object.owner_user_id,
                file_id=file_object.id,
                purpose="model_image",
            )
        )

    assert exc_info.value.code == "agent_model_asset_unavailable"


class FakeAgentFileAccessService:
    def __init__(self, *, file_id: UUID) -> None:
        self.file_id = file_id
        self.kwargs: dict[str, object] = {}

    async def resolve(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            file_id=self.file_id,
            content_type="image/png",
            original_filename="checkup.png",
            model_url="https://api.example.test/v1/model-assets/opaque-capability",
            expires_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )


class FakeFileRepository:
    def __init__(self, file_object: object | None) -> None:
        self.file_object = file_object

    async def get_for_owner(self, **_kwargs: object) -> object | None:
        return self.file_object


class FakeCapabilityStore:
    def __init__(self) -> None:
        self.capabilities: list[object] = []

    async def issue_or_refresh(self, capability: object) -> IssuedAgentAssetCapability:
        self.capabilities.append(capability)
        return IssuedAgentAssetCapability(
            token="opaque-capability",
            expires_at=datetime(2026, 7, 26, 0, 30, tzinfo=timezone.utc),
        )


def _file_object(*, content_type: str) -> SimpleNamespace:
    owner_user_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        owner_user_id=owner_user_id,
        object_key=f"users/{owner_user_id}/files/checkup",
        original_filename="checkup.png",
        content_type=content_type,
        size_bytes=128,
        status="active",
        deleted_at=None,
    )


def _app() -> FastAPI:
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )


@pytest.mark.parametrize(
    ("content_type", "purpose", "environment"),
    (
        ("image/png", "model_image", "local"),
        ("application/pdf", "model_file", "local"),
        ("image/png", "model_image", "staging"),
        ("application/pdf", "model_file", "staging"),
    ),
)
def test_model_asset_bytes_require_service_identity_and_owner_scope(
    content_type: str, purpose: str, environment: str,
) -> None:
    from app.modules.files.agent_router import get_agent_model_bytes_service

    file_object = _file_object(content_type=content_type)
    service = AgentModelBytesService(
        repository=OwnerScopedFakeFileRepository(file_object),
        object_storage=FakeObjectStorage(),
        max_bytes=10 * 1024 * 1024,
    )
    app = create_app(Settings(app_env="local", agent_runtime_service_api_key=SERVICE_KEY))
    app.state.settings = Settings(app_env=environment, agent_runtime_service_api_key=SERVICE_KEY)
    app.dependency_overrides[get_agent_model_bytes_service] = lambda: service
    client = TestClient(app)
    path = f"/v1/internal/agent/files/{file_object.id}/model-asset"
    params = {"actor_user_id": str(file_object.owner_user_id), "purpose": purpose}
    assert client.get(path, params=params).status_code == 401
    foreign = client.get(path, params={**params, "actor_user_id": str(uuid4())}, headers={"X-Service-Key": SERVICE_KEY})
    assert foreign.status_code == 422
    assert service.object_storage.keys == []
    valid = client.get(path, params=params, headers={"X-Service-Key": SERVICE_KEY})
    assert valid.status_code == 200
    assert valid.content == b"image-bytes"
    assert valid.headers["content-type"] == content_type
    assert valid.headers["cache-control"] == "private, no-store"
    assert service.object_storage.keys == [file_object.object_key]


@pytest.mark.parametrize(
    ("content_type", "purpose", "status", "size"),
    (
        ("application/pdf", "model_image", "active", 10),
        ("image/png", "model_file", "active", 10),
        ("text/plain", "model_file", "active", 10),
        ("image/png", "model_image", "deleted", 10),
        ("image/png", "model_image", "active", 11 * 1024 * 1024),
    ),
)
def test_model_asset_bytes_reject_invalid_purpose_deleted_and_oversized_files(
    content_type: str, purpose: str, status: str, size: int,
) -> None:
    file_object = _file_object(content_type=content_type)
    file_object.status = status
    file_object.size_bytes = size
    storage = FakeObjectStorage()
    service = AgentModelBytesService(repository=FakeFileRepository(file_object), object_storage=storage,
                                     max_bytes=10 * 1024 * 1024)
    with pytest.raises(ApiError):
        asyncio.run(service.fetch(owner_user_id=file_object.owner_user_id, file_id=file_object.id, purpose=purpose))
    assert storage.keys == []


def test_model_asset_bytes_rejects_empty_and_oversized_storage_responses() -> None:
    file_object = _file_object(content_type="application/pdf")
    for body in (b"", b"x" * (10 * 1024 * 1024 + 1)):
        storage = FakeObjectStorage()
        storage.body = body
        service = AgentModelBytesService(
            repository=OwnerScopedFakeFileRepository(file_object),
            object_storage=storage, max_bytes=10 * 1024 * 1024,
        )
        with pytest.raises(ApiError) as error:
            asyncio.run(service.fetch(
                owner_user_id=file_object.owner_user_id, file_id=file_object.id,
                purpose="model_file",
            ))
        assert error.value.code == "invalid_agent_attachment"


def test_legacy_local_image_endpoint_remains_compatible_but_not_available_in_staging() -> None:
    from app.modules.files.agent_router import get_agent_model_bytes_service

    file_object = _file_object(content_type="image/png")
    service = AgentModelBytesService(
        repository=OwnerScopedFakeFileRepository(file_object),
        object_storage=FakeObjectStorage(), max_bytes=10 * 1024 * 1024,
    )
    app = create_app(Settings(app_env="local", agent_runtime_service_api_key=SERVICE_KEY))
    app.dependency_overrides[get_agent_model_bytes_service] = lambda: service
    client = TestClient(app)
    path = f"/v1/internal/agent/files/{file_object.id}/model-image"
    params = {"actor_user_id": str(file_object.owner_user_id)}
    assert client.get(path, params=params).status_code == 401
    valid = client.get(path, params=params, headers={"X-Service-Key": SERVICE_KEY})
    assert valid.status_code == 200
    assert valid.content == b"image-bytes"
    assert valid.headers["content-type"] == "image/png"
    app.state.settings = Settings(app_env="staging", agent_runtime_service_api_key=SERVICE_KEY)
    assert client.get(path, params=params, headers={"X-Service-Key": SERVICE_KEY}).status_code == 404


def test_model_asset_bytes_endpoint_is_unavailable_in_production() -> None:
    from app.modules.files.agent_router import get_agent_model_bytes_service
    file_object = _file_object(content_type="image/png")
    app = _app()
    app.state.settings = Settings(app_env="production", agent_runtime_service_api_key=SERVICE_KEY)
    app.dependency_overrides[get_agent_model_bytes_service] = lambda: None
    response = TestClient(app).get(f"/v1/internal/agent/files/{file_object.id}/model-asset",
        params={"actor_user_id": str(file_object.owner_user_id), "purpose": "model_image"}, headers={"X-Service-Key": SERVICE_KEY})
    assert response.status_code == 404


class OwnerScopedFakeFileRepository(FakeFileRepository):
    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> object | None:
        if self.file_object is None or self.file_object.id != file_id or self.file_object.owner_user_id != owner_user_id:
            return None
        return self.file_object


class FakeObjectStorage:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.body = b"image-bytes"

    async def get_bytes(self, *, key: str) -> bytes:
        self.keys.append(key)
        return self.body
