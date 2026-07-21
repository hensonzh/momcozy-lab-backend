import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.agent_runtime.context.memory.service import AgentMemoryService
from app.agent_runtime.runs.models import AgentMemory, AgentMemorySettings, AgentMemorySnapshot


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
    assert memory.content == {"summary": "Prefers concise reminders", "sensitivity": "normal"}
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


def test_agent_memory_service_reads_only_minimal_precomputed_runtime_snapshot() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    repository.snapshots_by_owner[owner_user_id] = AgentMemorySnapshot(
        owner_user_id=owner_user_id,
        items=[
            {
                "memory_id": str(uuid4()),
                "memory_key": "communication.reminder_style",
                "memory_type": "communication_preference",
                "summary": "Prefers concise reminders",
                "confidence_score": 90,
            }
        ],
    )
    service = AgentMemoryService(repository=repository)

    snapshot = asyncio.run(service.get_runtime_snapshot(owner_user_id=owner_user_id, limit=5))

    assert snapshot == [
        {
            "memory_type": "communication_preference",
            "summary": "Prefers concise reminders",
        }
    ]
    assert repository.snapshot_reads == 1
    assert repository.settings_reads == 0


def test_agent_memory_service_runtime_snapshot_excludes_expired_items_without_another_query() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    repository.snapshots_by_owner[owner_user_id] = AgentMemorySnapshot(
        owner_user_id=owner_user_id,
        items=[
            {
                "memory_type": "communication_preference",
                "summary": "Expired preference",
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            },
            {
                "memory_type": "communication_preference",
                "summary": "Active preference",
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        ],
    )
    service = AgentMemoryService(repository=repository)

    snapshot = asyncio.run(service.get_runtime_snapshot(owner_user_id=owner_user_id))

    assert snapshot == [{"memory_type": "communication_preference", "summary": "Active preference"}]
    assert repository.snapshot_reads == 1
    assert repository.settings_reads == 0


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


def test_agent_memory_service_rejects_sensitive_memory_policy() -> None:
    service = AgentMemoryService(repository=FakeMemoryRepository())

    with pytest.raises(ApiError) as explicit_exc:
        asyncio.run(
            service.create_memory(
                owner_user_id=uuid4(),
                memory_type="user_preference",
                content={"summary": "User wants health facts remembered", "sensitivity": "health"},
            )
        )
    with pytest.raises(ApiError) as inferred_exc:
        asyncio.run(
            service.create_memory(
                owner_user_id=uuid4(),
                memory_type="user_preference",
                content={"summary": "Newborn fever should be remembered"},
            )
        )

    assert explicit_exc.value.code == "sensitive_memory_not_allowed"
    assert inferred_exc.value.code == "sensitive_memory_not_allowed"


def test_agent_memory_service_applies_retention_policy_and_excludes_expired_memories() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    service = AgentMemoryService(repository=repository)

    active = asyncio.run(
        service.create_memory(
            owner_user_id=owner_user_id,
            memory_type="user_preference",
            content={"summary": "Likes quiet reminders"},
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    expired = AgentMemory(
        id=uuid4(),
        owner_user_id=owner_user_id,
        memory_type="user_preference",
        content={"summary": "Expired", "sensitivity": "normal"},
        schema_version="v1",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        status="active",
    )
    repository.memories.append(expired)

    memories = asyncio.run(service.list_active_memories(owner_user_id=owner_user_id))

    assert active.expires_at is not None
    assert memories == [active]


def test_agent_memory_service_settings_disable_writes_and_runtime_projection() -> None:
    owner_user_id = uuid4()
    repository = FakeMemoryRepository()
    service = AgentMemoryService(repository=repository)

    default_settings = asyncio.run(service.get_settings(owner_user_id=owner_user_id))
    disabled_settings = asyncio.run(service.update_settings(owner_user_id=owner_user_id, memory_enabled=False))
    repository.memories.append(
        AgentMemory(
            id=uuid4(),
            owner_user_id=owner_user_id,
            memory_type="communication_preference",
            content={"summary": "Existing memory", "sensitivity": "normal"},
            schema_version="v1",
            status="active",
        )
    )
    projected = asyncio.run(service.list_active_memories(owner_user_id=owner_user_id))
    management_list = asyncio.run(service.list_active_memories(owner_user_id=owner_user_id, include_when_disabled=True))

    with pytest.raises(ApiError) as create_exc:
        asyncio.run(
            service.create_memory(
                owner_user_id=owner_user_id,
                memory_type="communication_preference",
                content={"summary": "New memory"},
            )
        )

    assert default_settings.memory_enabled is True
    assert disabled_settings.memory_enabled is False
    assert repository.snapshots_by_owner[owner_user_id].items == []
    assert projected == []
    assert len(management_list) == 1
    assert create_exc.value.code == "memory_disabled"


def test_agent_memory_service_archive_missing_memory_returns_not_found() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(AgentMemoryService(repository=FakeMemoryRepository()).archive_memory(owner_user_id=uuid4(), memory_id=uuid4()))

    assert exc_info.value.code == "not_found"


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.memories: list[AgentMemory] = []
        self.create_kwargs = {}
        self.settings_by_owner: dict[UUID, AgentMemorySettings] = {}
        self.snapshots_by_owner: dict[UUID, AgentMemorySnapshot] = {}
        self.settings_reads = 0
        self.snapshot_reads = 0

    async def get_memory_settings(self, *, owner_user_id):
        self.settings_reads += 1
        return self.settings_by_owner.get(owner_user_id)

    async def upsert_memory_settings(self, *, owner_user_id, memory_enabled):
        settings = self.settings_by_owner.get(owner_user_id)
        if settings is None:
            settings = AgentMemorySettings(owner_user_id=owner_user_id)
            self.settings_by_owner[owner_user_id] = settings
        settings.memory_enabled = memory_enabled
        settings.updated_at = datetime.now(timezone.utc)
        return settings

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
        now = datetime.now(timezone.utc)
        return [
            memory
            for memory in self.memories
            if memory.owner_user_id == owner_user_id
            and memory.status == "active"
            and (memory.expires_at is None or memory.expires_at > now)
            and (memory_type is None or memory.memory_type == memory_type)
        ][:limit]

    async def archive_memory(self, *, owner_user_id, memory_id, archived_at):
        for memory in self.memories:
            if memory.owner_user_id == owner_user_id and memory.id == memory_id and memory.status == "active":
                memory.status = "archived"
                memory.archived_at = archived_at
                return memory
        return None

    async def get_memory_snapshot(self, *, owner_user_id):
        self.snapshot_reads += 1
        return self.snapshots_by_owner.get(owner_user_id)

    async def upsert_memory_snapshot(self, *, owner_user_id, items, source_date=None, extractor_version=""):
        snapshot = self.snapshots_by_owner.get(owner_user_id)
        if snapshot is None:
            snapshot = AgentMemorySnapshot(owner_user_id=owner_user_id)
            self.snapshots_by_owner[owner_user_id] = snapshot
        snapshot.items = items
        snapshot.source_date = source_date
        snapshot.extractor_version = extractor_version
        snapshot.updated_at = datetime.now(timezone.utc)
        return snapshot
