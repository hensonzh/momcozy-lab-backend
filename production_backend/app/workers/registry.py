from __future__ import annotations

from .outbox import OutboxHandler
from ..infrastructure.object_storage import ObjectStorage
from ..modules.files.outbox_handlers import FileObjectDeleteHandler
from ..modules.files.service import FILE_OBJECT_DELETE_JOB


def build_outbox_handlers(*, object_storage: ObjectStorage) -> dict[str, OutboxHandler]:
    return {FILE_OBJECT_DELETE_JOB: FileObjectDeleteHandler(object_storage=object_storage)}
