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
from app.modules.files.agent_service import AgentFileAccessService


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
        "model_url": "https://assets.example.test/signed/checkup.png",
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
        object_storage=FakeObjectStorage(),
        url_ttl_seconds=3600,
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
        object_storage=FakeObjectStorage(),
        url_ttl_seconds=3600,
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


def test_agent_file_access_returns_bounded_https_url_and_expiry() -> None:
    file_object = _file_object(content_type="image/webp")
    storage = FakeObjectStorage()
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        object_storage=storage,
        url_ttl_seconds=3600,
        clock=lambda: datetime(2026, 7, 26, tzinfo=timezone.utc),
    )

    result = asyncio.run(
        service.resolve(
            owner_user_id=file_object.owner_user_id,
            file_id=file_object.id,
            purpose="model_image",
        )
    )

    assert result.file_id == file_object.id
    assert result.model_url == "https://assets.example.test/signed/checkup"
    assert result.expires_at == datetime(2026, 7, 26, 1, tzinfo=timezone.utc)
    assert storage.calls == [
        {
            "key": file_object.object_key,
            "expires_in_seconds": 3600,
        }
    ]


def test_agent_file_access_fails_closed_for_non_https_signed_url() -> None:
    file_object = _file_object(content_type="image/png")
    service = AgentFileAccessService(
        repository=FakeFileRepository(file_object),
        object_storage=FakeObjectStorage(url="http://storage.internal/checkup"),
        url_ttl_seconds=3600,
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.resolve(
                owner_user_id=file_object.owner_user_id,
                file_id=file_object.id,
                purpose="model_image",
            )
        )

    assert exc_info.value.code == "agent_file_url_unavailable"


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
            model_url="https://assets.example.test/signed/checkup.png",
            expires_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )


class FakeFileRepository:
    def __init__(self, file_object: object | None) -> None:
        self.file_object = file_object

    async def get_for_owner(self, **_kwargs: object) -> object | None:
        return self.file_object


class FakeObjectStorage:
    def __init__(
        self,
        *,
        url: str = "https://assets.example.test/signed/checkup",
    ) -> None:
        self.url = url
        self.calls: list[dict[str, object]] = []

    async def create_presigned_get_url(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.url


def _file_object(*, content_type: str) -> SimpleNamespace:
    owner_user_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        owner_user_id=owner_user_id,
        object_key=f"users/{owner_user_id}/files/checkup",
        original_filename="checkup.png",
        content_type=content_type,
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
