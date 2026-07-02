import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.files.models import FileObject
from production_backend.app.modules.files.service import FileService


def test_file_service_upload_stores_object_and_metadata() -> None:
    owner_user_id = uuid4()
    repository = FakeFileRepository()
    storage = FakeObjectStorage()
    service = FileService(repository=repository, object_storage=storage)

    created = asyncio.run(
        service.upload(
            owner_user_id=owner_user_id,
            filename="../photo.png",
            body=b"image",
            content_type="image/png",
        )
    )

    assert created.owner_user_id == owner_user_id
    assert created.original_filename == "photo.png"
    assert created.content_type == "image/png"
    assert created.size_bytes == 5
    assert storage.put_calls[0]["key"].startswith(f"users/{owner_user_id}/files/")
    assert repository.created_kwargs["owner_user_id"] == owner_user_id


def test_file_service_rejects_empty_upload() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage())

    with pytest.raises(ApiError, match="Uploaded file is empty"):
        asyncio.run(
            service.upload(
                owner_user_id=uuid4(),
                filename="empty.txt",
                body=b"",
                content_type="text/plain",
            )
        )


def test_file_service_get_for_owner_raises_not_found() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage())

    with pytest.raises(ApiError, match="File not found"):
        asyncio.run(service.get_for_owner(file_id=uuid4(), owner_user_id=uuid4()))


def test_file_service_upload_records_audit_and_completes_idempotency() -> None:
    owner_user_id = uuid4()
    repository = FakeFileRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = FileService(
        repository=repository,
        object_storage=FakeObjectStorage(),
        audit_service=audit_service,
        idempotency_service=idempotency_service,
    )

    created = asyncio.run(
        service.upload(
            owner_user_id=owner_user_id,
            filename="photo.png",
            body=b"image",
            content_type="image/png",
            request_id="req_123",
            idempotency_key="idem-1",
        )
    )

    assert idempotency_service.reserve_kwargs["actor_user_id"] == owner_user_id
    assert idempotency_service.reserve_kwargs["scope"] == "files.upload"
    assert idempotency_service.completed_response_ref == str(created.id)
    assert audit_service.record_kwargs["action"] == "files.upload"
    assert audit_service.record_kwargs["request_id"] == "req_123"


def test_file_service_upload_replays_completed_idempotency_without_storing_again() -> None:
    owner_user_id = uuid4()
    file_id = uuid4()
    existing_file = _file(owner_user_id=owner_user_id, file_id=file_id)
    repository = FakeFileRepository(file_to_return=existing_file)
    storage = FakeObjectStorage()
    service = FileService(
        repository=repository,
        object_storage=storage,
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(file_id)),
    )

    returned = asyncio.run(
        service.upload(
            owner_user_id=owner_user_id,
            filename="photo.png",
            body=b"image",
            content_type="image/png",
            idempotency_key="idem-1",
        )
    )

    assert returned is existing_file
    assert storage.put_calls == []


def test_file_service_upload_conflicts_when_replay_is_in_progress() -> None:
    service = FileService(
        repository=FakeFileRepository(),
        object_storage=FakeObjectStorage(),
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=""),
    )

    with pytest.raises(ApiError, match="still in progress"):
        asyncio.run(
            service.upload(
                owner_user_id=uuid4(),
                filename="photo.png",
                body=b"image",
                content_type="image/png",
                idempotency_key="idem-1",
            )
        )


class FakeFileRepository:
    def __init__(self, *, file_to_return=None) -> None:
        self.created_kwargs = {}
        self.file_to_return = file_to_return

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        return _file(
            owner_user_id=kwargs["owner_user_id"],
            object_key=kwargs["object_key"],
            original_filename=kwargs["original_filename"],
            content_type=kwargs["content_type"],
            size_bytes=kwargs["size_bytes"],
        )

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID):
        return self.file_to_return


class FakeObjectStorage:
    def __init__(self) -> None:
        self.put_calls = []
        self.delete_calls = []

    async def put_bytes(self, **kwargs):
        self.put_calls.append(kwargs)

    async def get_bytes(self, *, key: str):
        return b""

    async def delete(self, *, key: str):
        self.delete_calls.append(key)


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.response_ref = response_ref
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="files.upload",
            key="idem-1",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        self.record.actor_user_id = kwargs["actor_user_id"]
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        record.status = "completed"
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None


def _file(
    *,
    owner_user_id: UUID,
    file_id: UUID | None = None,
    object_key: str | None = None,
    original_filename: str = "photo.png",
    content_type: str = "image/png",
    size_bytes: int = 5,
) -> FileObject:
    return FileObject(
        id=file_id or uuid4(),
        owner_user_id=owner_user_id,
        object_key=object_key or f"users/{owner_user_id}/files/file/photo.png",
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        status="active",
    )
