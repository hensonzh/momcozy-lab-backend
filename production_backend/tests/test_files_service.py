import asyncio
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
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


class FakeFileRepository:
    def __init__(self) -> None:
        self.created_kwargs = {}
        self.file_to_return = None

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        return FileObject(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            object_key=kwargs["object_key"],
            original_filename=kwargs["original_filename"],
            content_type=kwargs["content_type"],
            size_bytes=kwargs["size_bytes"],
            status="active",
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
