import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.files.models import FileObject
from app.modules.files.vision_service import FileVisionService


def test_file_vision_service_local_stub_reads_owner_file_and_object_storage() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, content_type="image/png")
    storage = FakeObjectStorage(body=b"image-bytes")
    service = FileVisionService(
        repository=FakeFileRepository(file_object=file_object),
        object_storage=storage,
        settings=Settings(app_env="test", vision_provider="local_stub"),
    )

    events = asyncio.run(service.events_for_owner(file_id=file_object.id, owner_user_id=owner_user_id))

    assert [event.type for event in events] == ["vision.started", "vision.event", "vision.completed"]
    assert events[0].file_id == file_object.id
    assert events[0].payload["purpose"] == "general"
    assert events[1].payload["provider"] == "local_stub"
    assert events[1].payload["bytes_read"] == len(b"image-bytes")
    assert storage.get_kwargs == {"key": file_object.object_key}


def test_file_vision_service_returns_read_only_schedule_task_previews() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, content_type="image/png")
    repository = FakeFileRepository(file_object=file_object)
    storage = FakeObjectStorage(body=b"image-bytes")
    service = FileVisionService(
        repository=repository,
        object_storage=storage,
        settings=Settings(app_env="test", vision_provider="local_stub"),
    )

    events = asyncio.run(
        service.events_for_owner(
            file_id=file_object.id,
            owner_user_id=owner_user_id,
            purpose="schedule",
        )
    )

    assert [event.type for event in events] == [
        "vision.started",
        "vision.schedule_task.preview",
        "vision.schedule_task.preview",
        "vision.completed",
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert events[0].payload["purpose"] == "schedule"
    assert events[1].payload == {
        "provider": "local_stub",
        "purpose": "schedule",
        "time": "09:00",
        "event": "Pumping",
        "event_type": "pump",
    }
    assert events[2].payload["event_type"] == "breastfeed"
    assert events[-1].payload == {
        "purpose": "schedule",
        "event_count": 2,
        "bytes_read": len(b"image-bytes"),
    }
    assert repository.get_calls == [(file_object.id, owner_user_id)]
    assert not hasattr(repository, "create")


def test_file_vision_service_releases_owner_read_before_slow_dependencies() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, content_type="image/png")
    released = False

    async def release_read_transaction() -> None:
        nonlocal released
        released = True

    storage = FakeObjectStorage(body=b"image-bytes", before_read=lambda: released)
    service = FileVisionService(
        repository=FakeFileRepository(file_object=file_object),
        object_storage=storage,
        settings=Settings(app_env="test", vision_provider="local_stub"),
        release_read_transaction=release_read_transaction,
    )

    asyncio.run(service.events_for_owner(file_id=file_object.id, owner_user_id=owner_user_id))

    assert released is True


def test_file_vision_service_rejects_cross_owner_file() -> None:
    service = FileVisionService(
        repository=FakeFileRepository(file_object=None),
        object_storage=FakeObjectStorage(body=b"image-bytes"),
        settings=Settings(app_env="test", vision_provider="local_stub"),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.events_for_owner(file_id=uuid4(), owner_user_id=uuid4()))

    assert exc_info.value.code == "not_found"


def test_file_vision_service_rejects_non_image_file() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, content_type="application/pdf")
    service = FileVisionService(
        repository=FakeFileRepository(file_object=file_object),
        object_storage=FakeObjectStorage(body=b"pdf"),
        settings=Settings(app_env="test", vision_provider="local_stub"),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.events_for_owner(file_id=file_object.id, owner_user_id=owner_user_id))

    assert exc_info.value.code == "validation_failed"


def test_file_vision_service_returns_stable_disabled_error() -> None:
    owner_user_id = uuid4()
    file_object = _file(owner_user_id=owner_user_id, content_type="image/png")
    service = FileVisionService(
        repository=FakeFileRepository(file_object=file_object),
        object_storage=FakeObjectStorage(body=b"image-bytes"),
        settings=Settings(app_env="test", vision_provider="disabled"),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.events_for_owner(file_id=file_object.id, owner_user_id=owner_user_id))

    assert exc_info.value.code == "vision_provider_disabled"


class FakeFileRepository:
    def __init__(self, *, file_object: FileObject | None) -> None:
        self.file_object = file_object
        self.get_calls: list[tuple[UUID, UUID]] = []

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID):
        self.get_calls.append((file_id, owner_user_id))
        if self.file_object is None:
            return None
        if self.file_object.id != file_id or self.file_object.owner_user_id != owner_user_id:
            return None
        return self.file_object


class FakeObjectStorage:
    def __init__(self, *, body: bytes, before_read=None) -> None:
        self.body = body
        self.before_read = before_read
        self.get_kwargs = {}

    async def put_bytes(self, **kwargs):
        return None

    async def get_bytes(self, **kwargs):
        if self.before_read is not None:
            assert self.before_read() is True
        self.get_kwargs = kwargs
        return self.body

    async def delete(self, **kwargs):
        return None


def _file(*, owner_user_id: UUID, content_type: str) -> FileObject:
    file_id = uuid4()
    return FileObject(
        id=file_id,
        owner_user_id=owner_user_id,
        object_key=f"users/{owner_user_id}/files/{file_id}/photo.png",
        original_filename="photo.png",
        content_type=content_type,
        size_bytes=11,
        status="active",
    )
