import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.settings import Settings
from app.agent_runtime.context.memory.consolidation import (
    MemoryConsolidationApplyResult,
    MemoryConsolidationBatch,
    MemoryConsolidationPreparation,
)
from scripts import run_memory_consolidation as worker_module


def test_memory_consolidation_local_day_uses_configured_timezone() -> None:
    start, end = worker_module.local_day_utc_bounds(
        source_date=date(2026, 7, 10),
        timezone_name="Asia/Shanghai",
    )

    assert start == datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


def test_memory_consolidation_once_returns_disabled_without_opening_database() -> None:
    factory = FakeSessionFactory()

    result = asyncio.run(
        worker_module.run_memory_consolidation_once(
            settings=Settings(agent_memory_consolidation_enabled=False),
            session_factory=factory,
        )
    )

    assert result["status"] == "disabled"
    assert factory.opened == 0


def test_memory_consolidation_calls_model_between_short_database_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_user_id = uuid4()
    batch = MemoryConsolidationBatch(
        run_id=uuid4(),
        owner_user_id=owner_user_id,
        source_date=date(2026, 7, 10),
        source_hash="source-hash",
        extractor_version="memory-extractor-v1",
        messages=(),
        existing_memories=(),
    )
    factory = FakeSessionFactory()
    repository = FakeRepository(owner_user_id=owner_user_id)
    extractor = FakeExtractor(factory=factory)

    async def fake_prepare(**kwargs):
        assert factory.active == 1
        return MemoryConsolidationPreparation(status="ready", batch=batch)

    async def fake_apply(**kwargs):
        assert factory.active == 1
        return MemoryConsolidationApplyResult(
            status="completed",
            run_id=batch.run_id,
            upserted_count=1,
            archived_count=0,
            rejected_count=0,
        )

    monkeypatch.setattr(worker_module, "prepare_memory_consolidation", fake_prepare)
    monkeypatch.setattr(worker_module, "apply_memory_consolidation", fake_apply)

    result = asyncio.run(
        worker_module.run_memory_consolidation_once(
            settings=Settings(
                agent_memory_consolidation_enabled=True,
                openai_api_key="sk-test",
            ),
            source_date=date(2026, 7, 10),
            now=datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc),
            session_factory=factory,
            repository_factory=lambda session: repository,
            extractor=extractor,
        )
    )

    assert result["completed"] == 1
    assert result["upserted"] == 1
    assert extractor.calls == 1
    assert factory.active == 0
    assert factory.opened == 3


class FakeSessionFactory:
    def __init__(self) -> None:
        self.active = 0
        self.opened = 0

    def __call__(self):
        return FakeSession(factory=self)


class FakeSession:
    def __init__(self, *, factory: FakeSessionFactory) -> None:
        self.factory = factory

    async def __aenter__(self):
        self.factory.active += 1
        self.factory.opened += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.factory.active -= 1

    async def commit(self):
        return None

    async def rollback(self):
        return None


class FakeRepository:
    def __init__(self, *, owner_user_id) -> None:
        self.owner_user_id = owner_user_id

    async def list_conversation_owner_ids(self, **kwargs):
        return [self.owner_user_id]


class FakeExtractor:
    def __init__(self, *, factory: FakeSessionFactory) -> None:
        self.factory = factory
        self.calls = 0

    async def extract(self, *, batch):
        assert self.factory.active == 0
        self.calls += 1
        return []
