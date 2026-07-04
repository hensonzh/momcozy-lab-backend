import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.memory import AgentMemoryService
from production_backend.app.modules.agent_runtime.models import AgentMemory


def test_agent_memory_service_creates_owner_scoped_memory() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    service = AgentMemoryService(repository=repository)

    memory = asyncio.run(
        service.create_memory(
            owner_user_id=owner_user_id,
            memory_type="communication_preference",
            content={"summary": "Prefers concise reminders"},
            confidence_score=80,
        )
    )

    assert memory.owner_user_id == owner_user_id
    assert memory.memory_type == "communication_preference"
    assert memory.content == {"summary": "Prefers concise reminders"}
    assert memory.confidence_score == 80
    assert repository.create_kwargs["schema_version"] == "v1"


def test_agent_memory_service_lists_and_archives_active_memories() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    service = AgentMemoryService(repository=repository)
    memory = asyncio.run(
        service.create_memory(
            owner_user_id=owner_user_id,
            memory_type="user_preference",
            content={"summary": "Likes evening reminders"},
        )
    )

    memories = asyncio.run(service.list_active_memories(owner_user_id=owner_user_id, memory_type="user_preference"))
    archived = asyncio.run(service.archive_memory(owner_user_id=owner_user_id, memory_id=memory.id))

    assert memories == [memory]
    assert archived.status == "archived"
    assert archived.archived_at is not None


def test_agent_memory_service_rejects_unsupported_type_and_empty_content() -> None:
    service = AgentMemoryService(repository=FakeMemoryRepository())

    with pytest.raises(ApiError) as type_exc:
        asyncio.run(service.create_memory(owner_user_id=uuid4(), memory_type="medical_fact", content={"summary": "x"}))
    with pytest.raises(ApiError) as content_exc:
        asyncio.run(service.create_memory(owner_user_id=uuid4(), memory_type="user_preference", content={}))
    with pytest.raises(ApiError) as confidence_exc:
        asyncio.run(
            service.create_memory(
                owner_user_id=uuid4(),
                memory_type="user_preference",
                content={"summary": "x"},
                confidence_score=101,
            )
        )

    assert type_exc.value.code == "unsupported_memory_type"
    assert content_exc.value.code == "validation_failed"
    assert confidence_exc.value.code == "validation_failed"


def test_agent_memory_service_archive_missing_memory_returns_not_found() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(AgentMemoryService(repository=FakeMemoryRepository()).archive_memory(owner_user_id=uuid4(), memory_id=uuid4()))

    assert exc_info.value.code == "not_found"


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.memories: list[AgentMemory] = []
        self.create_kwargs = {}

    async def create_memory(self, **kwargs):
        self.create_kwargs = kwargs
        memory = AgentMemory(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            memory_type=kwargs["memory_type"],
            content=kwargs["content"],
            schema_version=kwargs["schema_version"],
            source_run_id=kwargs["source_run_id"],
            source_message_id=kwargs["source_message_id"],
            confidence_score=kwargs["confidence_score"],
            expires_at=kwargs["expires_at"],
            status="active",
        )
        self.memories.append(memory)
        return memory

    async def list_active_memories(self, *, owner_user_id, memory_type, limit):
        return [
            memory
            for memory in self.memories
            if memory.owner_user_id == owner_user_id and memory.status == "active" and (memory_type is None or memory.memory_type == memory_type)
        ][:limit]

    async def archive_memory(self, *, owner_user_id, memory_id, archived_at):
        for memory in self.memories:
            if memory.owner_user_id == owner_user_id and memory.id == memory_id and memory.status == "active":
                memory.status = "archived"
                memory.archived_at = archived_at
                return memory
        return None
