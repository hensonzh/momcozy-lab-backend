import asyncio

import pytest

from app.modules.audit.models import OutboxJob
from app.modules.files.outbox_handlers import FileObjectDeleteHandler
from app.modules.files.service import FILE_OBJECT_DELETE_JOB
from app.workers.errors import PermanentJobError
from app.workers.registry import build_outbox_handlers


def test_file_object_delete_handler_deletes_object_key() -> None:
    storage = FakeObjectStorage()
    handler = FileObjectDeleteHandler(object_storage=storage)
    job = OutboxJob(
        job_type=FILE_OBJECT_DELETE_JOB,
        payload={"object_key": "users/user-1/files/file-1/photo.png"},
        idempotency_key="job-1",
    )

    asyncio.run(handler(job))

    assert storage.deleted_keys == ["users/user-1/files/file-1/photo.png"]


def test_file_object_delete_handler_rejects_missing_object_key() -> None:
    handler = FileObjectDeleteHandler(object_storage=FakeObjectStorage())
    job = OutboxJob(job_type=FILE_OBJECT_DELETE_JOB, payload={}, idempotency_key="job-1")

    with pytest.raises(PermanentJobError, match="missing_object_key"):
        asyncio.run(handler(job))


def test_build_outbox_handlers_registers_file_cleanup_handler() -> None:
    handlers = build_outbox_handlers(object_storage=FakeObjectStorage())

    assert set(handlers) == {FILE_OBJECT_DELETE_JOB}


class FakeObjectStorage:
    def __init__(self) -> None:
        self.deleted_keys = []

    async def put_bytes(self, **kwargs):
        return None

    async def get_bytes(self, *, key: str):
        return b""

    async def delete(self, *, key: str):
        self.deleted_keys.append(key)
