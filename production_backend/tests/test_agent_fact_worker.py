import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from production_backend.app.modules.agent_runtime.facts.repository import FactExtractionJobClaim
from production_backend.app.modules.agent_runtime.facts.types import FactApplyResult, FactInput
from production_backend.app.modules.agent_runtime.facts.worker import AgentFactExtractionWorker
from production_backend.app.modules.agent_runtime.facts import worker as worker_module


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def test_fact_worker_extracts_between_short_database_sessions_and_stores_sanitized_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch)
    extractor = SuccessfulExtractor(harness=harness)

    result = asyncio.run(harness.worker(extractor=extractor).process(harness.claim()))

    assert result.status == "ready_to_apply"
    assert harness.factory.active == 0
    assert harness.factory.opened == 2
    assert extractor.calls == 1
    assert harness.job.status == "ready_to_apply"
    assert harness.job.stage == "apply"
    assert harness.job.candidates == [
        {"fact_key": "profile.age", "value": 35, "subject": "self"}
    ]


def test_fact_worker_applies_candidates_only_after_source_run_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch, stage="apply", run_status="completed")
    harness.job.candidates = [{"fact_key": "profile.age", "value": 35, "subject": "self"}]

    result = asyncio.run(harness.worker().process(harness.claim()))

    assert result == worker_module.FactExtractionProcessResult(status="completed", applied_count=1)
    assert harness.job.status == "completed"
    assert harness.job.candidates == []
    assert harness.applied_candidates == [
        {
            "owner_user_id": harness.owner_user_id,
            "source_message_id": harness.message.id,
            "observed_at": NOW,
            "candidates": [{"fact_key": "profile.age", "value": 35, "subject": "self"}],
            "request_id": "request-1",
        }
    ]


@pytest.mark.parametrize("run_status", ["queued", "running", "waiting_for_confirmation"])
def test_fact_worker_defers_apply_while_source_run_is_active(
    monkeypatch: pytest.MonkeyPatch,
    run_status: str,
) -> None:
    harness = WorkerHarness(monkeypatch, stage="apply", run_status=run_status)
    harness.job.candidates = [{"fact_key": "profile.age", "value": 35, "subject": "self"}]

    result = asyncio.run(harness.worker().process(harness.claim()))

    assert result.status == "deferred"
    assert harness.job.status == "ready_to_apply"
    assert harness.job.stage == "apply"
    assert harness.job.lease_token == ""
    assert harness.applied_candidates == []


@pytest.mark.parametrize("run_status", ["waiting_for_confirmation", "completed"])
def test_fact_worker_scrubs_candidates_after_absolute_retention_deadline(
    monkeypatch: pytest.MonkeyPatch,
    run_status: str,
) -> None:
    harness = WorkerHarness(monkeypatch, stage="apply", run_status=run_status)
    harness.job.created_at = NOW - timedelta(days=15)
    harness.job.candidates = [{"fact_key": "profile.age", "value": 35, "subject": "self"}]

    result = asyncio.run(harness.worker().process(harness.claim()))

    assert result.status == "expired"
    assert harness.job.status == "cancelled"
    assert harness.job.error_code == "source_run_nonterminal_deadline"
    assert harness.job.candidates == []
    assert harness.job.completed_at == NOW
    assert harness.applied_candidates == []


def test_fact_worker_skips_model_work_after_absolute_retention_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch, stage="extract", run_status="completed")
    harness.job.created_at = NOW - timedelta(days=15)
    extractor = SuccessfulExtractor(harness=harness)

    result = asyncio.run(harness.worker(extractor=extractor).process(harness.claim()))

    assert result.status == "expired"
    assert extractor.calls == 0
    assert harness.job.status == "cancelled"
    assert harness.job.candidates == []


def test_fact_worker_discards_extracted_candidates_when_user_opts_out_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch)

    extracted = asyncio.run(harness.worker(extractor=SuccessfulExtractor(harness=harness)).process(harness.claim()))
    assert extracted.status == "ready_to_apply"
    harness.lock(stage="apply", lease_token="lease-2")
    harness.memory_enabled = False

    result = asyncio.run(harness.worker().process(harness.claim()))

    assert result.status == "skipped_disabled"
    assert harness.job.status == "skipped_disabled"
    assert harness.job.candidates == []
    assert harness.applied_candidates == []


def test_fact_worker_provider_failure_is_rescheduled_then_dead_lettered_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch, attempts=1, max_attempts=2)
    extractor = FailingExtractor(harness=harness)
    worker = harness.worker(extractor=extractor)

    first = asyncio.run(worker.process(harness.claim()))

    assert first.status == "queued"
    assert harness.job.status == "queued"
    assert harness.job.error_code == "ProviderUnavailable"

    harness.job.attempts = 2
    harness.lock(stage="extract", lease_token="lease-2")
    second = asyncio.run(worker.process(harness.claim()))

    assert second.status == "dead_lettered"
    assert harness.job.status == "dead_lettered"
    assert harness.job.error_code == "ProviderUnavailable"
    assert extractor.calls == 2


def test_fact_worker_rejects_a_stale_extract_claim_before_model_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch)
    extractor = SuccessfulExtractor(harness=harness)
    stale_claim = FactExtractionJobClaim(
        job_id=harness.job.id,
        lease_token="stale-lease",
        stage="extract",
        attempts=1,
    )

    result = asyncio.run(harness.worker(extractor=extractor).process(stale_claim))

    assert result.status == "stale_lease"
    assert extractor.calls == 0


def test_fact_worker_does_not_publish_candidates_after_losing_lease_during_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch)
    extractor = LeaseStealingExtractor(harness=harness)

    result = asyncio.run(harness.worker(extractor=extractor).process(harness.claim()))

    assert result.status == "stale_lease"
    assert harness.job.status == "locked"
    assert harness.job.candidates == []


def test_fact_worker_fail_closes_when_source_run_does_not_belong_to_job_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WorkerHarness(monkeypatch)
    harness.run_owner_user_id = uuid4()
    extractor = SuccessfulExtractor(harness=harness)

    result = asyncio.run(harness.worker(extractor=extractor).process(harness.claim()))

    assert result.status == "queued"
    assert harness.job.error_code == "RuntimeError"
    assert extractor.calls == 0


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class WorkerState:
    owner_user_id: UUID = field(default_factory=uuid4)
    run_owner_user_id: UUID | None = None
    memory_enabled: bool = True
    applied_candidates: list[dict] = field(default_factory=list)


class WorkerHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        stage: str = "extract",
        run_status: str = "completed",
        attempts: int = 1,
        max_attempts: int = 3,
    ) -> None:
        self.state = WorkerState()
        self.owner_user_id = self.state.owner_user_id
        self.run_owner_user_id = self.owner_user_id
        self.run = SimpleNamespace(
            id=uuid4(),
            thread_id=uuid4(),
            status=run_status,
        )
        self.message = SimpleNamespace(
            id=uuid4(),
            run_id=self.run.id,
            role="user",
            sequence=2,
            content={"text": "我35岁"},
            created_at=NOW,
        )
        self.dialogue = [
            SimpleNamespace(role="assistant", sequence=1, content={"text": "请问您多大？"}),
            self.message,
        ]
        self.job = SimpleNamespace(
            id=uuid4(),
            owner_user_id=self.owner_user_id,
            source_run_id=self.run.id,
            source_message_id=self.message.id,
            request_id="request-1",
            trace_id="trace-1",
            stage=stage,
            status="locked",
            attempts=attempts,
            max_attempts=max_attempts,
            lease_token="lease-1",
            candidates=[],
            extracted_count=0,
            applied_count=0,
            rejected_count=0,
            next_attempt_at=NOW,
            locked_until=NOW,
            error_code="",
            created_at=NOW,
            completed_at=None,
        )
        self.factory = FakeSessionFactory(harness=self)
        monkeypatch.setattr(worker_module, "_utcnow", lambda: NOW)
        monkeypatch.setattr(worker_module, "AgentFactRepository", FakeFactRepository)
        monkeypatch.setattr(worker_module, "AgentMemoryRepository", FakeMemoryRepository)
        monkeypatch.setattr(worker_module, "AgentMemoryService", FakeMemoryService)
        monkeypatch.setattr(worker_module, "AgentRuntimeRepository", FakeRuntimeRepository)
        monkeypatch.setattr(worker_module, "AuditRepository", lambda _session: object())
        monkeypatch.setattr(worker_module, "AuditService", lambda *, repository: object())
        monkeypatch.setattr(worker_module, "AgentFactService", FakeFactService)

    @property
    def memory_enabled(self) -> bool:
        return self.state.memory_enabled

    @memory_enabled.setter
    def memory_enabled(self, value: bool) -> None:
        self.state.memory_enabled = value

    @property
    def applied_candidates(self) -> list[dict]:
        return self.state.applied_candidates

    def claim(self) -> FactExtractionJobClaim:
        return FactExtractionJobClaim(
            job_id=self.job.id,
            lease_token=self.job.lease_token,
            stage=self.job.stage,
            attempts=self.job.attempts,
        )

    def lock(self, *, stage: str, lease_token: str) -> None:
        self.job.stage = stage
        self.job.status = "locked"
        self.job.lease_token = lease_token
        self.job.locked_until = NOW

    def worker(self, *, extractor=None) -> AgentFactExtractionWorker:
        return AgentFactExtractionWorker(
            session_factory=self.factory,
            extractor=extractor or SuccessfulExtractor(harness=self),
            retry_base_seconds=0.1,
            apply_poll_seconds=0.05,
        )


class FakeSessionFactory:
    def __init__(self, *, harness: WorkerHarness) -> None:
        self.harness = harness
        self.active = 0
        self.opened = 0

    def __call__(self):
        return FakeSession(factory=self)


class FakeSession:
    def __init__(self, *, factory: FakeSessionFactory) -> None:
        self.factory = factory
        self.harness = factory.harness

    async def __aenter__(self):
        self.factory.active += 1
        self.factory.opened += 1
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.factory.active -= 1

    async def commit(self) -> None:
        return None


class FakeFactRepository:
    def __init__(self, session: FakeSession) -> None:
        self.harness = session.harness

    async def get_claimed_extraction(self, *, job_id, lease_token, for_update=False):
        job = self.harness.job
        if job.id != job_id or job.status != "locked" or job.lease_token != lease_token:
            return None
        return job

    async def mark_ready_to_apply(self, *, job, candidates, extracted_count, rejected_count, next_attempt_at):
        job.status = "ready_to_apply"
        job.stage = "apply"
        job.candidates = candidates
        job.extracted_count = extracted_count
        job.rejected_count = rejected_count
        job.next_attempt_at = next_attempt_at
        job.locked_until = None
        job.lease_token = ""
        job.error_code = ""

    async def defer_apply(self, *, job, next_attempt_at):
        job.status = "ready_to_apply"
        job.next_attempt_at = next_attempt_at
        job.locked_until = None
        job.lease_token = ""

    async def mark_completed(self, *, job, applied_count, rejected_count, completed_at):
        job.status = "completed"
        job.applied_count = applied_count
        job.rejected_count = rejected_count
        job.candidates = []
        job.completed_at = completed_at
        job.locked_until = None
        job.lease_token = ""

    async def mark_skipped_disabled(self, *, job, completed_at):
        job.status = "skipped_disabled"
        job.candidates = []
        job.completed_at = completed_at
        job.locked_until = None
        job.lease_token = ""
        job.error_code = "memory_disabled"

    async def mark_retention_expired(self, *, job, completed_at):
        job.status = "cancelled"
        job.candidates = []
        job.completed_at = completed_at
        job.locked_until = None
        job.lease_token = ""
        job.error_code = "source_run_nonterminal_deadline"

    async def reschedule_or_dead_letter(self, *, job, next_attempt_at, error_code, completed_at):
        if job.attempts >= job.max_attempts:
            job.status = "dead_lettered"
            job.completed_at = completed_at
        else:
            job.status = "queued"
            job.next_attempt_at = next_attempt_at
        job.stage = "extract"
        job.candidates = []
        job.locked_until = None
        job.lease_token = ""
        job.error_code = error_code


class FakeMemoryRepository:
    def __init__(self, session: FakeSession) -> None:
        self.harness = session.harness


class FakeMemoryService:
    def __init__(self, *, repository: FakeMemoryRepository) -> None:
        self.harness = repository.harness

    async def is_memory_enabled(self, *, owner_user_id):
        assert owner_user_id == self.harness.owner_user_id
        return self.harness.memory_enabled


class FakeRuntimeRepository:
    def __init__(self, session: FakeSession) -> None:
        self.harness = session.harness

    async def get_run(self, *, run_id):
        return self.harness.run if run_id == self.harness.run.id else None

    async def get_run_for_owner(self, *, run_id, owner_user_id):
        if run_id != self.harness.run.id or owner_user_id != self.harness.run_owner_user_id:
            return None
        return self.harness.run

    async def get_latest_user_message_for_run(self, *, run_id):
        return self.harness.message if run_id == self.harness.run.id else None

    async def list_messages_for_thread(self, *, thread_id, limit):
        assert thread_id == self.harness.run.thread_id
        return self.harness.dialogue[-limit:]


class FakeFactService:
    def __init__(self, *, repository: FakeFactRepository, **_kwargs) -> None:
        self.harness = repository.harness

    async def apply_candidate_payloads(self, **kwargs):
        self.harness.applied_candidates.append(kwargs)
        return FactApplyResult(applied_count=len(kwargs["candidates"]), rejected_count=0)


class SuccessfulExtractor:
    def __init__(self, *, harness: WorkerHarness) -> None:
        self.harness = harness
        self.calls = 0

    async def extract(self, *, context):
        assert self.harness.factory.active == 0
        assert context.owner_user_id == self.harness.owner_user_id
        assert context.source_text == "我35岁"
        assert context.trace_id == "trace-1"
        assert context.recent_dialogue == (("assistant", "请问您多大？"), ("user", "我35岁"))
        self.calls += 1
        return [FactInput("profile.age", 35, "explicit", "我35岁", "self")]


class FailingExtractor(SuccessfulExtractor):
    async def extract(self, *, context):
        await super().extract(context=context)
        raise ProviderUnavailable("provider unavailable")


class LeaseStealingExtractor(SuccessfulExtractor):
    async def extract(self, *, context):
        result = await super().extract(context=context)
        self.harness.job.lease_token = "new-owner-lease"
        return result
