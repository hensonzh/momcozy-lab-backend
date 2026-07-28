from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.files.agent_asset_capability import AgentAssetCapability
from app.modules.files.agent_service import AgentModelAssetService
from app.modules.files.model_asset_router import get_agent_model_asset_service


TOKEN = "a" * 43


def test_model_asset_capability_get_is_unauthenticated_and_returns_no_store_bytes() -> None:
    service = FakeAgentModelAssetService()
    app = _app()
    app.dependency_overrides[get_agent_model_asset_service] = lambda: service

    response = TestClient(app).get(f"/v1/model-assets/{TOKEN}")

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert service.tokens == [TOKEN]


def test_model_asset_capability_path_is_rate_limited_and_never_exempted() -> None:
    app = _app(
        rate_limit_enabled=True,
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
    )
    app.dependency_overrides[get_agent_model_asset_service] = lambda: FakeAgentModelAssetService()
    client = TestClient(app)

    assert client.get(f"/v1/model-assets/{TOKEN}").status_code == 200
    assert client.get(f"/v1/model-assets/{TOKEN}").status_code == 429


def test_model_asset_openapi_declares_binary_media_and_opaque_failure_contract() -> None:
    operation = _app().openapi()["paths"][
        "/v1/model-assets/{token}"
    ]["get"]

    assert set(operation["responses"]["200"]["content"]) == {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
    }
    assert set(operation["responses"]) >= {
        "200",
        "404",
        "422",
        "429",
        "503",
    }
    assert "security" not in operation


def test_model_asset_capability_token_is_redacted_from_request_and_error_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()
    app.dependency_overrides[get_agent_model_asset_service] = lambda: ExplodingAgentModelAssetService()

    with caplog.at_level(logging.INFO):
        response = TestClient(app, raise_server_exceptions=False).get(
            f"/v1/model-assets/{TOKEN}"
        )

    assert response.status_code == 500
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("production_backend.")
    )
    assert TOKEN not in application_logs
    assert "/v1/model-assets/<redacted>" in application_logs


def test_model_asset_fetch_revalidates_authoritative_file_scope_before_storage_read() -> None:
    capability = _capability()
    file_object = SimpleNamespace(
        id=capability.file_id,
        owner_user_id=capability.owner_user_id,
        object_key=capability.object_key,
        content_type=capability.content_type,
        status="active",
        deleted_at=None,
    )
    storage = FakeObjectStorage()
    service = AgentModelAssetService(
        repository=FakeFileRepository(file_object),
        object_storage=storage,
        capability_store=FakeCapabilityLookup(capability),
    )

    result = asyncio.run(service.fetch(token=TOKEN))

    assert result.body == b"image-bytes"
    assert result.content_type == "image/png"
    assert storage.keys == [capability.object_key]


@pytest.mark.parametrize(
    "mutation",
    (
        {"status": "deleted"},
        {"deleted_at": object()},
        {"object_key": "users/other/file.png"},
        {"content_type": "application/pdf"},
    ),
)
def test_model_asset_fetch_returns_one_opaque_not_found_for_revoked_or_drifted_mapping(
    mutation: dict[str, object],
) -> None:
    capability = _capability()
    file_values: dict[str, object] = {
        "id": capability.file_id,
        "owner_user_id": capability.owner_user_id,
        "object_key": capability.object_key,
        "content_type": capability.content_type,
        "status": "active",
        "deleted_at": None,
    }
    file_values.update(mutation)
    storage = FakeObjectStorage()
    service = AgentModelAssetService(
        repository=FakeFileRepository(SimpleNamespace(**file_values)),
        object_storage=storage,
        capability_store=FakeCapabilityLookup(capability),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.fetch(token=TOKEN))

    assert exc_info.value.code == "not_found"
    assert exc_info.value.status == 404
    assert storage.keys == []


def test_model_asset_fetch_returns_same_opaque_not_found_for_unknown_token() -> None:
    service = AgentModelAssetService(
        repository=FakeFileRepository(None),
        object_storage=FakeObjectStorage(),
        capability_store=FakeCapabilityLookup(None),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.fetch(token=TOKEN))

    assert exc_info.value.code == "not_found"
    assert exc_info.value.status == 404


class FakeAgentModelAssetService:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def fetch(self, *, token: str) -> object:
        self.tokens.append(token)
        return SimpleNamespace(body=b"image-bytes", content_type="image/png")


class ExplodingAgentModelAssetService:
    async def fetch(self, *, token: str) -> object:
        del token
        raise RuntimeError("storage failure")


class FakeCapabilityLookup:
    def __init__(self, capability: AgentAssetCapability | None) -> None:
        self.capability = capability

    async def get(self, token: str) -> AgentAssetCapability | None:
        assert token == TOKEN
        return self.capability


class FakeFileRepository:
    def __init__(self, file_object: object | None) -> None:
        self.file_object = file_object
        self.calls: list[dict[str, object]] = []

    async def get_for_owner(self, **kwargs: object) -> object | None:
        self.calls.append(kwargs)
        return self.file_object


class FakeObjectStorage:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def get_bytes(self, *, key: str) -> bytes:
        self.keys.append(key)
        return b"image-bytes"


def _capability() -> AgentAssetCapability:
    owner_user_id = uuid4()
    return AgentAssetCapability(
        owner_user_id=owner_user_id,
        file_id=uuid4(),
        object_key=f"users/{owner_user_id}/files/photo.png",
        purpose="model_image",
        content_type="image/png",
    )


def _app(**overrides: object) -> FastAPI:
    return create_app(
        Settings(
            app_env="test",
            **overrides,
        )
    )
