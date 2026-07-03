from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...core.settings import Settings
from ...infrastructure.object_storage import ObjectStorage
from .repository import FileRepository


@dataclass(frozen=True)
class FileVisionEvent:
    type: str
    sequence: int
    file_id: UUID
    payload: dict[str, Any]


class FileVisionService:
    def __init__(
        self,
        *,
        repository: FileRepository,
        object_storage: ObjectStorage,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.object_storage = object_storage
        self.settings = settings

    async def events_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> list[FileVisionEvent]:
        file_object = await self.repository.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            raise ApiError(code="not_found", message="File not found.", status=404)
        if not file_object.content_type.startswith("image/"):
            raise ApiError(code="validation_failed", message="Vision analysis requires an image file.", status=422)
        if self.settings.vision_provider == "disabled":
            raise ApiError(code="vision_provider_disabled", message="Vision provider is not configured.", status=503)

        body = await self._read_object_bytes(key=file_object.object_key)
        return [
            FileVisionEvent(
                type="vision.started",
                sequence=1,
                file_id=file_object.id,
                payload={
                    "content_type": file_object.content_type,
                    "original_filename": file_object.original_filename,
                    "size_bytes": file_object.size_bytes,
                },
            ),
            FileVisionEvent(
                type="vision.event",
                sequence=2,
                file_id=file_object.id,
                payload={
                    "provider": self.settings.vision_provider,
                    "summary": "Vision provider local stub processed the image.",
                    "bytes_read": len(body),
                },
            ),
            FileVisionEvent(
                type="vision.completed",
                sequence=3,
                file_id=file_object.id,
                payload={"event_count": 1},
            ),
        ]

    async def _read_object_bytes(self, *, key: str) -> bytes:
        try:
            return await self.object_storage.get_bytes(key=key)
        except FileNotFoundError as exc:
            raise ApiError(code="file_object_missing", message="Uploaded file object is missing.", status=404) from exc
        except Exception as exc:
            raise ApiError(code="dependency_failed", message="Object storage read failed.", status=503) from exc
