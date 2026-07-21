import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.infrastructure.object_storage import StoredObject
from app.modules.files.models import FileObject
from app.modules.files.service import FileService
from app.modules.files.vision_service import FileVisionService


def test_image_upload_vision_and_delete_main_flow() -> None:
    owner_user_id = uuid4()
    repository = InMemoryFileRepository()
    storage = InMemoryObjectStorage()
    audit_service = FlowAuditService()
    file_service = FileService(
        repository=repository,
        object_storage=storage,
        audit_service=audit_service,
    )
    vision_service = FileVisionService(
        repository=repository,
        object_storage=storage,
        settings=Settings(app_env="test", vision_provider="local_stub"),
    )

    uploaded = asyncio.run(
        file_service.upload(
            owner_user_id=owner_user_id,
            filename="../photo.png",
            body=b"image-bytes",
            content_type="image/png",
            request_id="req_upload",
        )
    )
    listed = asyncio.run(file_service.list_for_owner(owner_user_id=owner_user_id, limit=10))
    detail = asyncio.run(file_service.get_for_owner(file_id=uploaded.id, owner_user_id=owner_user_id))
    vision_events = asyncio.run(vision_service.events_for_owner(file_id=uploaded.id, owner_user_id=owner_user_id))

    assert uploaded.original_filename == "photo.png"
    assert [item.id for item in listed] == [uploaded.id]
    assert detail.id == uploaded.id
    assert [event.type for event in vision_events] == ["vision.started", "vision.event", "vision.completed"]
    assert vision_events[1].payload["bytes_read"] == len(b"image-bytes")
    assert storage.objects[uploaded.object_key] == b"image-bytes"

    asyncio.run(file_service.delete_for_owner(file_id=uploaded.id, owner_user_id=owner_user_id, request_id="req_delete"))

    assert asyncio.run(file_service.list_for_owner(owner_user_id=owner_user_id, limit=10)) == []
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(vision_service.events_for_owner(file_id=uploaded.id, owner_user_id=owner_user_id))
    assert exc_info.value.code == "not_found"
    assert uploaded.object_key not in storage.objects
    assert [entry["action"] for entry in audit_service.entries] == ["files.upload", "files.delete"]


class InMemoryFileRepository:
    def __init__(self) -> None:
        self.files: list[FileObject] = []

    async def create(self, **kwargs):
        file_object = FileObject(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            object_key=kwargs["object_key"],
            original_filename=kwargs["original_filename"],
            content_type=kwargs["content_type"],
            size_bytes=kwargs["size_bytes"],
            status="active",
        )
        self.files.append(file_object)
        return file_object

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID):
        return next(
            (
                file_object
                for file_object in self.files
                if file_object.id == file_id and file_object.owner_user_id == owner_user_id and file_object.deleted_at is None
            ),
            None,
        )

    async def list_for_owner(self, *, owner_user_id: UUID, limit: int):
        files = [
            file_object
            for file_object in self.files
            if file_object.owner_user_id == owner_user_id and file_object.status == "active" and file_object.deleted_at is None
        ]
        return files[:limit]

    async def soft_delete_for_owner(self, *, file_id: UUID, owner_user_id: UUID, deleted_at):
        file_object = await self.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            return None
        file_object.status = "deleted"
        file_object.deleted_at = deleted_at
        return file_object


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def put_bytes(self, *, key: str, body: bytes, content_type: str):
        self.objects[key] = body
        self.content_types[key] = content_type
        return StoredObject(key=key, uri=f"memory://{key}", size_bytes=len(body), content_type=content_type)

    async def get_bytes(self, *, key: str):
        return self.objects[key]

    async def delete(self, *, key: str):
        self.objects.pop(key, None)


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
