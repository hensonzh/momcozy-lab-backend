import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.agent_runtime.context.facts.repository import AgentFactRepository


NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def test_fact_repository_retries_apply_without_repeating_extraction() -> None:
    repository = AgentFactRepository(FakeSession())
    job = _job(stage="apply", attempts=1, max_attempts=3)

    asyncio.run(
        repository.reschedule_or_dead_letter(
            job=job,
            next_attempt_at=NOW,
            error_code="DatabaseUnavailable",
            completed_at=NOW,
        )
    )

    assert job.status == "ready_to_apply"
    assert job.stage == "apply"
    assert job.attempts == 2
    assert job.candidates == [{"fact_key": "profile.age", "value": 35, "subject": "self"}]


def test_fact_repository_bounds_apply_retries_and_scrubs_dead_letter_payload() -> None:
    repository = AgentFactRepository(FakeSession())
    job = _job(stage="apply", attempts=2, max_attempts=3)

    asyncio.run(
        repository.reschedule_or_dead_letter(
            job=job,
            next_attempt_at=NOW,
            error_code="DatabaseUnavailable",
            completed_at=NOW,
        )
    )

    assert job.status == "dead_lettered"
    assert job.stage == "apply"
    assert job.attempts == 3
    assert job.candidates == []
    assert job.completed_at == NOW


def test_fact_repository_dead_letters_expired_claim_after_attempt_budget() -> None:
    exhausted = _claim_job(stage="extract", attempts=3, max_attempts=3)
    recoverable = _claim_job(stage="apply", attempts=1, max_attempts=3)
    session = FakeClaimSession([exhausted, recoverable])
    repository = AgentFactRepository(session)

    claims = asyncio.run(
        repository.claim_due_extractions(
            now=NOW,
            locked_until=NOW,
            limit=10,
        )
    )

    assert exhausted.status == "dead_lettered"
    assert exhausted.error_code == "lease_attempts_exhausted"
    assert exhausted.candidates == []
    assert len(claims) == 1
    assert claims[0].job_id == recoverable.id
    assert claims[0].attempts == 2


def test_fact_repository_fencing_query_requires_unexpired_lease() -> None:
    session = CapturingScalarSession()
    repository = AgentFactRepository(session)

    result = asyncio.run(
        repository.get_claimed_extraction(
            job_id=uuid4(),
            lease_token="lease",
            for_update=True,
        )
    )

    assert result is None
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "locked_until IS NOT NULL" in sql
    assert "locked_until > now()" in sql
    assert "FOR UPDATE" in sql


def test_fact_repository_scrubs_candidates_when_retention_deadline_expires() -> None:
    repository = AgentFactRepository(FakeSession())
    job = _job(stage="apply", attempts=1, max_attempts=3)

    asyncio.run(repository.mark_retention_expired(job=job, completed_at=NOW))

    assert job.status == "cancelled"
    assert job.candidates == []
    assert job.locked_until is None
    assert job.lease_token == ""
    assert job.error_code == "source_run_nonterminal_deadline"
    assert job.completed_at == NOW


def _job(*, stage: str, attempts: int, max_attempts: int) -> SimpleNamespace:
    return SimpleNamespace(
        stage=stage,
        attempts=attempts,
        max_attempts=max_attempts,
        status="locked",
        candidates=[{"fact_key": "profile.age", "value": 35, "subject": "self"}],
        next_attempt_at=None,
        locked_until=NOW,
        lease_token="lease",
        error_code="",
        completed_at=None,
    )


class FakeSession:
    async def flush(self) -> None:
        return None


def _claim_job(*, stage: str, attempts: int, max_attempts: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        status="locked",
        stage=stage,
        attempts=attempts,
        max_attempts=max_attempts,
        candidates=[{"fact_key": "profile.age", "value": 35, "subject": "self"}],
        locked_until=NOW,
        lease_token="old-lease",
        error_code="",
        completed_at=None,
        started_at=None,
    )


class FakeScalarRows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeClaimSession:
    def __init__(self, jobs) -> None:
        self.jobs = jobs

    async def scalars(self, _statement):
        return FakeScalarRows(self.jobs)

    async def flush(self) -> None:
        return None


class CapturingScalarSession:
    def __init__(self) -> None:
        self.statement = None

    async def scalar(self, statement):
        self.statement = statement
        return None
