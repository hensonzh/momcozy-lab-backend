from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...core.settings import Settings
from ...infrastructure.object_storage import ObjectStorage
from .repository import FileRepository
from .vision_providers import VisionProvider, VisionPurpose, create_vision_provider


@dataclass(frozen=True)
class FileVisionEvent:
    type: str
    sequence: int
    file_id: UUID
    payload: dict[str, Any]


@dataclass(frozen=True)
class _FileVisionSource:
    id: UUID
    object_key: str
    original_filename: str
    content_type: str
    size_bytes: int


class FileVisionService:
    def __init__(
        self,
        *,
        repository: FileRepository,
        object_storage: ObjectStorage,
        settings: Settings,
        provider: VisionProvider | None = None,
        release_read_transaction: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.repository = repository
        self.object_storage = object_storage
        self.settings = settings
        self.provider = provider or create_vision_provider(settings)
        self.release_read_transaction = release_read_transaction

    async def events_for_owner(
        self,
        *,
        file_id: UUID,
        owner_user_id: UUID,
        purpose: VisionPurpose = "general",
    ) -> list[FileVisionEvent]:
        file_object = await self.repository.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            raise ApiError(code="not_found", message="File not found.", status=404)
        if not file_object.content_type.startswith("image/"):
            raise ApiError(code="validation_failed", message="Vision analysis requires an image file.", status=422)
        source = _FileVisionSource(
            id=file_object.id,
            object_key=file_object.object_key,
            original_filename=file_object.original_filename,
            content_type=file_object.content_type,
            size_bytes=file_object.size_bytes,
        )
        if self.release_read_transaction is not None:
            await self.release_read_transaction()
        self.provider.ensure_available()

        body = await self._read_object_bytes(key=source.object_key)
        analysis = await self.provider.analyze_image(
            body=body,
            content_type=source.content_type,
            original_filename=source.original_filename,
            purpose=purpose,
        )
        events = [
            FileVisionEvent(
                type="vision.started",
                sequence=1,
                file_id=source.id,
                payload={
                    "purpose": purpose,
                    "content_type": source.content_type,
                    "original_filename": source.original_filename,
                    "size_bytes": source.size_bytes,
                },
            )
        ]
        if purpose == "schedule":
            events.extend(
                FileVisionEvent(
                    type="vision.schedule_task.preview",
                    sequence=index + 2,
                    file_id=source.id,
                    payload={
                        "provider": analysis.provider,
                        "purpose": purpose,
                        **task.model_dump(),
                    },
                )
                for index, task in enumerate(analysis.schedule_tasks)
            )
            event_count = len(analysis.schedule_tasks)
        else:
            events.append(
                FileVisionEvent(
                    type="vision.event",
                    sequence=2,
                    file_id=source.id,
                    payload={
                        "provider": analysis.provider,
                        "purpose": purpose,
                        "summary": analysis.summary,
                        "bytes_read": analysis.bytes_read,
                    },
                )
            )
            event_count = 1
        events.append(
            FileVisionEvent(
                type="vision.completed",
                sequence=len(events) + 1,
                file_id=source.id,
                payload={
                    "purpose": purpose,
                    "event_count": event_count,
                    "bytes_read": analysis.bytes_read,
                },
            )
        )
        return events

    async def _read_object_bytes(self, *, key: str) -> bytes:
        try:
            return await self.object_storage.get_bytes(key=key)
        except FileNotFoundError as exc:
            raise ApiError(code="file_object_missing", message="Uploaded file object is missing.", status=404) from exc
        except Exception as exc:
            raise ApiError(code="dependency_failed", message="Object storage read failed.", status=503) from exc
