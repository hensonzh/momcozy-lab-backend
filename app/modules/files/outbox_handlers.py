from __future__ import annotations

from ...infrastructure.object_storage import ObjectStorage
from ...workers.errors import PermanentJobError
from ..audit.models import OutboxJob


class FileObjectDeleteHandler:
    def __init__(self, *, object_storage: ObjectStorage) -> None:
        self.object_storage = object_storage

    async def __call__(self, job: OutboxJob) -> None:
        object_key = str(job.payload.get("object_key") or "").strip()
        if not object_key:
            raise PermanentJobError("missing_object_key")
        await self.object_storage.delete(key=object_key)
