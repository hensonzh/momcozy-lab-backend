from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from .models import FileObject
from .repository import FileRepository


class FileService:
    def __init__(self, *, repository: FileRepository, object_storage: ObjectStorage) -> None:
        self.repository = repository
        self.object_storage = object_storage

    async def upload(
        self,
        *,
        owner_user_id: UUID,
        filename: str,
        body: bytes,
        content_type: str,
    ) -> FileObject:
        if not body:
            raise ApiError(code="validation_failed", message="Uploaded file is empty.", status=422)

        normalized_filename = _safe_filename(filename)
        normalized_content_type = content_type or "application/octet-stream"
        object_key = f"users/{owner_user_id}/files/{uuid4().hex}/{normalized_filename}"
        await self.object_storage.put_bytes(key=object_key, body=body, content_type=normalized_content_type)

        try:
            return await self.repository.create(
                owner_user_id=owner_user_id,
                object_key=object_key,
                original_filename=normalized_filename,
                content_type=normalized_content_type,
                size_bytes=len(body),
            )
        except Exception:
            await self.object_storage.delete(key=object_key)
            raise

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> FileObject:
        file_object = await self.repository.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            raise ApiError(code="not_found", message="File not found.", status=404)
        return file_object


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(str(filename or "").strip()).name
    return name or "upload.bin"
