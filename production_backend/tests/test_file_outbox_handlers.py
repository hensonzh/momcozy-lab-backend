import asyncio

import pytest

from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB
from production_backend.app.modules.files.outbox_handlers import FileObjectDeleteHandler
from production_backend.app.modules.files.service import FILE_OBJECT_DELETE_JOB
from production_backend.app.workers.errors import PermanentJobError
from production_backend.app.workers.registry import build_outbox_handlers


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

    assert FILE_OBJECT_DELETE_JOB in handlers
    assert AGENT_ACTION_APPLY_JOB not in handlers


def test_build_outbox_handlers_registers_agent_action_handler_when_repository_is_available() -> None:
    handlers = build_outbox_handlers(object_storage=FakeObjectStorage(), agent_runtime_repository=object())

    assert AGENT_ACTION_APPLY_JOB in handlers


class FakeObjectStorage:
    def __init__(self) -> None:
        self.deleted_keys = []

    async def put_bytes(self, **kwargs):
        return None

    async def get_bytes(self, *, key: str):
        return b""

    async def delete(self, *, key: str):
        self.deleted_keys.append(key)
