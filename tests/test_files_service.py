import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.files.models import FileObject
from app.modules.files.service import FileService


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


def test_file_service_rejects_oversized_upload() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage(), max_upload_bytes=4)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.upload(
                owner_user_id=uuid4(),
                filename="large.txt",
                body=b"large",
                content_type="text/plain",
            )
        )

    assert exc_info.value.code == "payload_too_large"
    assert exc_info.value.status == 413
    assert exc_info.value.details == {"max_bytes": 4}


def test_file_service_get_for_owner_raises_not_found() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage())

    with pytest.raises(ApiError, match="File not found"):
        asyncio.run(service.get_for_owner(file_id=uuid4(), owner_user_id=uuid4()))


def test_file_service_reads_owner_scoped_content() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id)
    repository = FakeFileRepository(file_to_return=file_object)
    storage = FakeObjectStorage(get_body=b"image-bytes")
    service = FileService(repository=repository, object_storage=storage)

    content = asyncio.run(
        service.read_content_for_owner(
            file_id=file_object.id,
            owner_user_id=owner_user_id,
        )
    )

    assert content.file_object is file_object
    assert content.body == b"image-bytes"
    assert storage.get_calls == [file_object.object_key]


def test_file_service_lists_files_for_owner() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id)
    repository = FakeFileRepository(files_to_return=[file_object])
    service = FileService(repository=repository, object_storage=FakeObjectStorage())

    files = asyncio.run(service.list_for_owner(owner_user_id=owner_user_id, limit=10))

    assert files == [file_object]
    assert repository.list_kwargs["owner_user_id"] == owner_user_id
    assert repository.list_kwargs["limit"] == 10


def test_file_service_rejects_invalid_list_limit() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage())

    with pytest.raises(ApiError, match="limit"):
        asyncio.run(service.list_for_owner(owner_user_id=uuid4(), limit=101))


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


def test_file_service_delete_soft_deletes_object_and_records_audit() -> None:
    owner_user_id = uuid4()
    file_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, file_id=file_id, object_key="users/u/files/f/photo.png")
    repository = FakeFileRepository(file_to_delete=file_object)
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    capability_store = FakeAgentAssetCapabilityStore()
    storage = FakeObjectStorage()
    service = FileService(
        repository=repository,
        object_storage=storage,
        audit_service=audit_service,
        idempotency_service=idempotency_service,
        agent_asset_capability_store=capability_store,
    )

    asyncio.run(
        service.delete_for_owner(
            file_id=file_id,
            owner_user_id=owner_user_id,
            request_id="req_delete",
            idempotency_key="idem-delete",
        )
    )

    assert file_object.status == "deleted"
    assert repository.delete_kwargs["file_id"] == file_id
    assert storage.delete_calls == ["users/u/files/f/photo.png"]
    assert capability_store.invalidate_kwargs == {
        "owner_user_id": owner_user_id,
        "file_id": file_id,
        "object_key": "users/u/files/f/photo.png",
    }
    assert audit_service.record_kwargs["action"] == "files.delete"
    assert audit_service.record_kwargs["details"] == {"object_cleanup": "deleted"}
    assert idempotency_service.completed_response_ref == str(file_id)


def test_file_service_delete_replays_completed_idempotency_without_deleting_again() -> None:
    repository = FakeFileRepository()
    service = FileService(
        repository=repository,
        object_storage=FakeObjectStorage(),
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(uuid4())),
    )

    asyncio.run(service.delete_for_owner(file_id=uuid4(), owner_user_id=uuid4(), idempotency_key="idem-delete"))

    assert repository.delete_kwargs == {}


def test_file_service_delete_raises_not_found() -> None:
    service = FileService(repository=FakeFileRepository(), object_storage=FakeObjectStorage())

    with pytest.raises(ApiError, match="File not found"):
        asyncio.run(service.delete_for_owner(file_id=uuid4(), owner_user_id=uuid4()))


def test_file_service_delete_does_not_report_success_when_object_cleanup_fails() -> None:
    owner_user_id = uuid4()
    file_id = uuid4()
    repository = FakeFileRepository(
        file_to_delete=_file(
            owner_user_id=owner_user_id,
            file_id=file_id,
            object_key="users/u/files/f/photo.png",
        )
    )
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = FileService(
        repository=repository,
        object_storage=FakeObjectStorage(delete_error=RuntimeError("storage unavailable")),
        audit_service=audit_service,
        idempotency_service=idempotency_service,
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        asyncio.run(
            service.delete_for_owner(
                file_id=file_id,
                owner_user_id=owner_user_id,
                request_id="req_delete",
                idempotency_key="idem-delete",
            )
        )

    assert audit_service.record_kwargs == {}
    assert idempotency_service.completed_response_ref == ""


class FakeFileRepository:
    def __init__(self, *, file_to_return=None, files_to_return=None, file_to_delete=None) -> None:
        self.created_kwargs = {}
        self.file_to_return = file_to_return
        self.files_to_return = files_to_return or []
        self.file_to_delete = file_to_delete
        self.list_kwargs = {}
        self.delete_kwargs = {}

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

    async def list_for_owner(self, **kwargs):
        self.list_kwargs = kwargs
        return self.files_to_return

    async def soft_delete_for_owner(self, **kwargs):
        self.delete_kwargs = kwargs
        if self.file_to_delete is not None:
            self.file_to_delete.status = "deleted"
            self.file_to_delete.deleted_at = kwargs["deleted_at"]
        return self.file_to_delete


class FakeObjectStorage:
    def __init__(self, *, get_body: bytes = b"", delete_error: Exception | None = None) -> None:
        self.put_calls = []
        self.get_calls = []
        self.get_body = get_body
        self.delete_calls = []
        self.delete_error = delete_error

    async def put_bytes(self, **kwargs):
        self.put_calls.append(kwargs)

    async def get_bytes(self, *, key: str):
        self.get_calls.append(key)
        return self.get_body

    async def delete(self, *, key: str):
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append(key)


class FakeAgentAssetCapabilityStore:
    def __init__(self) -> None:
        self.invalidate_kwargs: dict[str, object] = {}

    async def invalidate(self, **kwargs: object) -> None:
        self.invalidate_kwargs = kwargs


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
