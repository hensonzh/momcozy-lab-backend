from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.files.models import FileObject
from production_backend.app.modules.files.router import get_file_service


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


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

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

    async def upload(self, **kwargs):
        self.upload_kwargs = kwargs
        return self._file(owner_user_id=kwargs["owner_user_id"])

    async def get_for_owner(self, **kwargs):
        self.get_kwargs = kwargs
        return self._file(owner_user_id=kwargs["owner_user_id"])

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
