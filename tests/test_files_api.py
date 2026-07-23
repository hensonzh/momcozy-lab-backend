from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.files.models import FileObject
from app.modules.files.router import get_file_service, get_file_vision_service
from app.modules.files.service import FileContent
from app.modules.files.vision_service import FileVisionEvent


def test_file_upload_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post(
        "/v1/files/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_file_upload_uses_current_user_owner_scope() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/files/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["owner_user_id"] == str(user_id)
    assert fake_service.upload_kwargs["owner_user_id"] == user_id
    assert fake_service.upload_kwargs["body"] == b"hello"


def test_file_upload_rejects_oversized_body_before_service_call() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test", file_upload_max_bytes=4))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/files/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.json()["error"]["details"] == {"max_bytes": 4}
    assert fake_service.upload_kwargs == {}


def test_file_upload_passes_request_id_and_idempotency_key() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/files/upload",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"X-Request-ID": "req_test", "Idempotency-Key": " idem-1 "},
    )

    assert response.status_code == 201
    assert fake_service.upload_kwargs["request_id"] == "req_test"
    assert fake_service.upload_kwargs["idempotency_key"] == "idem-1"


def test_file_detail_uses_current_user_owner_scope() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).get(f"/v1/files/{fake_service.file_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(fake_service.file_id)
    assert fake_service.get_kwargs["owner_user_id"] == user_id


def test_file_content_uses_current_user_owner_scope_and_preserves_mime_type() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).get(f"/v1/files/{fake_service.file_id}/content")

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert fake_service.read_content_kwargs == {
        "file_id": fake_service.file_id,
        "owner_user_id": user_id,
    }


def test_file_list_uses_current_user_owner_scope_and_limit() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).get("/v1/files?limit=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == str(fake_service.file_id)
    assert fake_service.list_kwargs["owner_user_id"] == user_id
    assert fake_service.list_kwargs["limit"] == 10


def test_file_delete_uses_current_user_request_id_and_idempotency_key() -> None:
    user_id = uuid4()
    fake_service = FakeFileService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_service] = lambda: fake_service

    response = TestClient(app).delete(
        f"/v1/files/{fake_service.file_id}",
        headers={"X-Request-ID": "req_delete", "Idempotency-Key": " idem-delete "},
    )

    assert response.status_code == 204
    assert fake_service.delete_kwargs["owner_user_id"] == user_id
    assert fake_service.delete_kwargs["request_id"] == "req_delete"
    assert fake_service.delete_kwargs["idempotency_key"] == "idem-delete"


def test_file_vision_stream_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test", vision_provider="local_stub"))).get(
        f"/v1/files/{uuid4()}/vision/events/stream"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_file_vision_stream_uses_current_user_owner_scope_and_sse_contract() -> None:
    user_id = uuid4()
    fake_service = FakeFileVisionService()
    app = create_app(Settings(app_env="test", vision_provider="local_stub"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_vision_service] = lambda: fake_service

    response = TestClient(app).get(f"/v1/files/{fake_service.file_id}/vision/events/stream?purpose=schedule")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: vision.started" in response.text
    assert "event: vision.completed" in response.text
    assert f'"file_id":"{fake_service.file_id}"' in response.text
    assert "token" not in response.request.url.query.decode()
    assert fake_service.events_kwargs["owner_user_id"] == user_id
    assert fake_service.events_kwargs["file_id"] == fake_service.file_id
    assert fake_service.events_kwargs["purpose"] == "schedule"


def test_file_vision_stream_rejects_unknown_purpose_before_service_call() -> None:
    user_id = uuid4()
    fake_service = FakeFileVisionService()
    app = create_app(Settings(app_env="test", vision_provider="local_stub"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_vision_service] = lambda: fake_service

    response = TestClient(app).get(f"/v1/files/{fake_service.file_id}/vision/events/stream?purpose=calendar-write")

    assert response.status_code == 422
    assert fake_service.events_kwargs == {}


def test_file_vision_stream_returns_provider_disabled_error() -> None:
    user_id = uuid4()
    app = create_app(Settings(app_env="test", vision_provider="disabled"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_file_vision_service] = lambda: DisabledFileVisionService()

    response = TestClient(app).get(f"/v1/files/{uuid4()}/vision/events/stream")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vision_provider_disabled"


def _override_current_user(app, user_id: UUID) -> None:
    from app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakeFileService:
    def __init__(self) -> None:
        self.file_id = uuid4()
        self.upload_kwargs = {}
        self.get_kwargs = {}
        self.list_kwargs = {}
        self.delete_kwargs = {}
        self.read_content_kwargs = {}

    async def upload(self, **kwargs):
        self.upload_kwargs = kwargs
        return self._file(owner_user_id=kwargs["owner_user_id"])

    async def get_for_owner(self, **kwargs):
        self.get_kwargs = kwargs
        return self._file(owner_user_id=kwargs["owner_user_id"])

    async def list_for_owner(self, **kwargs):
        self.list_kwargs = kwargs
        return [self._file(owner_user_id=kwargs["owner_user_id"])]

    async def read_content_for_owner(self, **kwargs):
        self.read_content_kwargs = kwargs
        return FileContent(
            file_object=FileObject(
                id=self.file_id,
                owner_user_id=kwargs["owner_user_id"],
                object_key=f"users/{kwargs['owner_user_id']}/files/{self.file_id}/image.png",
                original_filename="image.png",
                content_type="image/png",
                size_bytes=11,
                status="active",
            ),
            body=b"image-bytes",
        )

    async def delete_for_owner(self, **kwargs):
        self.delete_kwargs = kwargs

    def _file(self, *, owner_user_id: UUID) -> FileObject:
        return FileObject(
            id=self.file_id,
            owner_user_id=owner_user_id,
            object_key=f"users/{owner_user_id}/files/{self.file_id}/hello.txt",
            original_filename="hello.txt",
            content_type="text/plain",
            size_bytes=5,
            status="active",
        )


class FakeFileVisionService:
    def __init__(self) -> None:
        self.file_id = uuid4()
        self.events_kwargs = {}

    async def events_for_owner(self, **kwargs):
        self.events_kwargs = kwargs
        return [
            FileVisionEvent(
                type="vision.started",
                sequence=1,
                file_id=kwargs["file_id"],
                payload={"provider": "local_stub"},
            ),
            FileVisionEvent(
                type="vision.completed",
                sequence=2,
                file_id=kwargs["file_id"],
                payload={"event_count": 0},
            ),
        ]


class DisabledFileVisionService:
    async def events_for_owner(self, **kwargs):
        raise ApiError(code="vision_provider_disabled", message="Vision provider is not configured.", status=503)
