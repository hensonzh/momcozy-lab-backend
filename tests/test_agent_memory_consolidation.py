import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.modules.agent_runtime.memory.consolidation import (
    AgentMemoryExtractor,
    MemoryCandidate,
    MemoryConsolidationBatch,
    MemoryExistingItem,
    MemorySourceMessage,
    apply_memory_consolidation,
    prepare_memory_consolidation,
)
from app.modules.agent_runtime.memory.service import AgentMemoryService
from app.modules.agent_runtime.models import (
    AgentMemory,
    AgentMemoryConsolidationRun,
    AgentMemorySettings,
    AgentMemorySnapshot,
    AgentMessage,
)
from app.modules.agent_runtime.sdk import SdkNodeResult


def test_memory_extractor_keeps_only_safe_user_evidenced_operations() -> None:
    owner_user_id = uuid4()
    existing_id = uuid4()
    batch = MemoryConsolidationBatch(
        run_id=uuid4(),
        owner_user_id=owner_user_id,
        source_date=date(2026, 7, 10),
        source_hash="source-hash",
        extractor_version="memory-extractor-v1",
        messages=(
            _source_message(index=0, role="user", text="以后提醒我时请简短一点"),
            _source_message(index=1, role="assistant", text="你喜欢晚上九点提醒"),
            _source_message(index=2, role="user", text="我不再需要晚间提醒了"),
        ),
        existing_memories=(
            MemoryExistingItem(
                memory_id=existing_id,
                memory_key="communication.evening_reminder",
                memory_type="communication_preference",
                summary="Prefers evening reminders",
            ),
        ),
    )
    runner = FakeModelRunner(
        {
            "operations": [
                {
                    "operation": "upsert",
                    "memory_key": "communication.concise_reminders",
                    "memory_type": "communication_preference",
                    "summary": "偏好简短提醒",
                    "confidence_score": 90,
                    "evidence_index": 0,
                    "expires_in_days": None,
                },
                {
                    "operation": "upsert",
                    "memory_key": "communication.evening_reminder",
                    "memory_type": "communication_preference",
                    "summary": "偏好晚上九点提醒",
                    "confidence_score": 95,
                    "evidence_index": 1,
                    "expires_in_days": None,
                },
                {
                    "operation": "archive",
                    "memory_key": "communication.evening_reminder",
                    "memory_type": "communication_preference",
                    "summary": "",
                    "confidence_score": 95,
                    "evidence_index": 2,
                    "expires_in_days": None,
                },
                {
                    "operation": "upsert",
                    "memory_key": "health.newborn_fever",
                    "memory_type": "stable_care_preference",
                    "summary": "记住宝宝发烧",
                    "confidence_score": 95,
                    "evidence_index": 2,
                    "expires_in_days": None,
                },
            ]
        }
    )

    candidates = asyncio.run(AgentMemoryExtractor(model_runner=runner).extract(batch=batch))

    assert [(item.operation, item.memory_key) for item in candidates] == [
        ("upsert", "communication.concise_reminders"),
        ("archive", "communication.evening_reminder"),
    ]
    assert candidates[0].source_message_id == batch.messages[0].message_id
    assert candidates[1].source_message_id == batch.messages[2].message_id
    assert runner.requests[0].tools == ()
    assert runner.requests[0].response_text_format["name"] == "nightly_memory_operations"


def test_prepare_memory_consolidation_is_idempotent_for_same_source_hash() -> None:
    owner_user_id = uuid4()
    repository = FakeConsolidationRepository(owner_user_id=owner_user_id)
    now = datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)

    first = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=now - timedelta(days=1),
            range_end=now,
            extractor_version="memory-extractor-v1",
            now=now,
        )
    )
    repository.runs[0].status = "completed"
    repository.runs[0].completed_at = now
    second = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=now - timedelta(days=1),
            range_end=now,
            extractor_version="memory-extractor-v1",
            now=now,
        )
    )

    assert first.status == "ready"
    assert first.batch is not None
    assert second.status == "already_completed"
    assert len(repository.runs) == 1


def test_prepare_memory_consolidation_skips_disabled_user() -> None:
    owner_user_id = uuid4()
    repository = FakeConsolidationRepository(owner_user_id=owner_user_id, memory_enabled=False)

    prepared = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=datetime(2026, 7, 10, tzinfo=timezone.utc),
            range_end=datetime(2026, 7, 11, tzinfo=timezone.utc),
            extractor_version="memory-extractor-v1",
        )
    )

    assert prepared.status == "disabled"
    assert prepared.batch is None
    assert repository.runs == []


def test_apply_memory_consolidation_upserts_archives_and_refreshes_snapshot_once() -> None:
    owner_user_id = uuid4()
    repository = FakeConsolidationRepository(owner_user_id=owner_user_id)
    existing = repository.memories[0]
    now = datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)
    prepared = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=now - timedelta(days=1),
            range_end=now,
            extractor_version="memory-extractor-v1",
            now=now,
        )
    )
    assert prepared.batch is not None
    user_message = prepared.batch.messages[0]
    candidates = asyncio.run(
        AgentMemoryExtractor(
            model_runner=FakeModelRunner(
                {
                    "operations": [
                        {
                            "operation": "archive",
                            "memory_key": existing.memory_key,
                            "memory_type": existing.memory_type,
                            "summary": "",
                            "confidence_score": 90,
                            "evidence_index": 0,
                            "expires_in_days": None,
                        },
                        {
                            "operation": "upsert",
                            "memory_key": "communication.concise_reminders",
                            "memory_type": "communication_preference",
                            "summary": "偏好简短提醒",
                            "confidence_score": 92,
                            "evidence_index": 0,
                            "expires_in_days": 180,
                        },
                    ]
                }
            )
        ).extract(batch=prepared.batch)
    )

    result = asyncio.run(
        apply_memory_consolidation(
            repository=repository,
            memory_service=AgentMemoryService(repository=repository),
            batch=prepared.batch,
            candidates=candidates,
            now=now,
        )
    )

    assert result.status == "completed"
    assert result.upserted_count == 1
    assert result.archived_count == 1
    assert existing.status == "archived"
    created = next(item for item in repository.memories if item.memory_key == "communication.concise_reminders")
    assert created.source_message_id == user_message.message_id
    assert created.expires_at == now + timedelta(days=180)
    assert repository.snapshot_writes == 1
    assert repository.snapshots_by_owner[owner_user_id].items[0]["memory_key"] == "communication.concise_reminders"
    assert repository.runs[0].status == "completed"

    repeated = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=now - timedelta(days=1),
            range_end=now,
            extractor_version="memory-extractor-v1",
            now=now,
        )
    )
    assert repeated.status == "already_completed"
    assert len(repository.runs) == 1


def test_apply_memory_consolidation_honors_user_opt_out_after_extraction() -> None:
    owner_user_id = uuid4()
    repository = FakeConsolidationRepository(owner_user_id=owner_user_id)
    now = datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)
    prepared = asyncio.run(
        prepare_memory_consolidation(
            repository=repository,
            owner_user_id=owner_user_id,
            source_date=date(2026, 7, 10),
            range_start=now - timedelta(days=1),
            range_end=now,
            now=now,
        )
    )
    assert prepared.batch is not None
    repository.settings.memory_enabled = False
    source = prepared.batch.messages[0]

    result = asyncio.run(
        apply_memory_consolidation(
            repository=repository,
            memory_service=AgentMemoryService(repository=repository),
            batch=prepared.batch,
            candidates=[
                _candidate(
                    memory_key="communication.concise_reminders",
                    source=source,
                )
            ],
            now=now,
        )
    )

    assert result.status == "disabled"
    assert all(item.memory_key != "communication.concise_reminders" for item in repository.memories)
    assert repository.snapshot_writes == 0
    assert repository.runs[0].status == "completed"


class FakeModelRunner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests = []

    async def run_reasoning(self, request):
        self.requests.append(request)
        return SdkNodeResult(final_text=json.dumps(self.payload, ensure_ascii=False))

    def supports_tool_namespaces(self) -> bool:
        return False


class FakeConsolidationRepository:
    def __init__(self, *, owner_user_id: UUID, memory_enabled: bool = True) -> None:
        self.owner_user_id = owner_user_id
        self.settings = AgentMemorySettings(owner_user_id=owner_user_id, memory_enabled=memory_enabled)
        self.messages = [
            AgentMessage(
                id=uuid4(),
                thread_id=uuid4(),
                run_id=uuid4(),
                role="user",
                message_type="text",
                content={"text": "以后提醒我时请简短一点"},
                status="completed",
                sequence=1,
                created_at=datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
            )
        ]
        self.memories = [
            AgentMemory(
                id=uuid4(),
                owner_user_id=owner_user_id,
                memory_key="communication.evening_reminder",
                memory_type="communication_preference",
                content={"summary": "偏好晚间提醒", "sensitivity": "normal"},
                confidence_score=85,
                status="active",
            )
        ]
        self.runs: list[AgentMemoryConsolidationRun] = []
        self.snapshots_by_owner: dict[UUID, AgentMemorySnapshot] = {}
        self.snapshot_writes = 0

    async def get_memory_settings(self, *, owner_user_id):
        return self.settings if owner_user_id == self.owner_user_id else None

    async def list_completed_conversation_messages(self, *, owner_user_id, range_start, range_end, limit):
        return self.messages[:limit] if owner_user_id == self.owner_user_id else []

    async def list_active_memories(self, *, owner_user_id, memory_type, limit):
        return [
            item
            for item in self.memories
            if item.owner_user_id == owner_user_id
            and item.status == "active"
            and (memory_type is None or item.memory_type == memory_type)
        ][:limit]

    async def get_consolidation_run(self, *, owner_user_id, source_date, source_hash, extractor_version):
        return next(
            (
                item
                for item in self.runs
                if item.owner_user_id == owner_user_id
                and item.source_date == source_date
                and item.source_hash == source_hash
                and item.extractor_version == extractor_version
            ),
            None,
        )

    async def create_consolidation_run(self, **kwargs):
        run = AgentMemoryConsolidationRun(id=uuid4(), status="extracting", **kwargs)
        self.runs.append(run)
        return run

    async def restart_consolidation_run(self, *, run, started_at, input_message_count):
        run.status = "extracting"
        run.started_at = started_at
        run.input_message_count = input_message_count
        return run

    async def complete_consolidation_run(self, *, run_id, completed_at, upserted_count, archived_count, rejected_count):
        run = next(item for item in self.runs if item.id == run_id)
        run.status = "completed"
        run.completed_at = completed_at
        run.upserted_count = upserted_count
        run.archived_count = archived_count
        run.rejected_count = rejected_count
        return run

    async def upsert_memory_by_key(self, **kwargs):
        memory = next(
            (
                item
                for item in self.memories
                if item.owner_user_id == kwargs["owner_user_id"] and item.memory_key == kwargs["memory_key"]
            ),
            None,
        )
        created = memory is None
        if memory is None:
            memory = AgentMemory(id=uuid4(), owner_user_id=kwargs["owner_user_id"], memory_key=kwargs["memory_key"])
            self.memories.append(memory)
        memory.memory_type = kwargs["memory_type"]
        memory.content = kwargs["content"]
        memory.schema_version = kwargs["schema_version"]
        memory.source_run_id = kwargs["source_run_id"]
        memory.source_message_id = kwargs["source_message_id"]
        memory.confidence_score = kwargs["confidence_score"]
        memory.expires_at = kwargs["expires_at"]
        memory.status = "active"
        memory.archived_at = None
        return memory, created

    async def archive_memory_by_key(self, *, owner_user_id, memory_key, archived_at):
        memory = next(
            (
                item
                for item in self.memories
                if item.owner_user_id == owner_user_id and item.memory_key == memory_key and item.status == "active"
            ),
            None,
        )
        if memory is not None:
            memory.status = "archived"
            memory.archived_at = archived_at
        return memory

    async def get_memory_snapshot(self, *, owner_user_id):
        return self.snapshots_by_owner.get(owner_user_id)

    async def upsert_memory_snapshot(self, *, owner_user_id, items, source_date=None, extractor_version=""):
        self.snapshot_writes += 1
        snapshot = self.snapshots_by_owner.get(owner_user_id)
        if snapshot is None:
            snapshot = AgentMemorySnapshot(owner_user_id=owner_user_id)
            self.snapshots_by_owner[owner_user_id] = snapshot
        snapshot.items = items
        snapshot.source_date = source_date
        snapshot.extractor_version = extractor_version
        return snapshot


def _source_message(*, index: int, role: str, text: str) -> MemorySourceMessage:
    return MemorySourceMessage(
        index=index,
        message_id=uuid4(),
        run_id=uuid4(),
        role=role,
        text=text,
        created_at=datetime(2026, 7, 10, 8 + index, tzinfo=timezone.utc),
    )


def _candidate(*, memory_key: str, source: MemorySourceMessage):
    return MemoryCandidate(
        operation="upsert",
        memory_key=memory_key,
        memory_type="communication_preference",
        summary="偏好简短提醒",
        confidence_score=90,
        source_message_id=source.message_id,
        source_run_id=source.run_id,
        evidence_index=source.index,
        expires_in_days=None,
    )
