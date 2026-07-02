import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.modules.audit.outbox import OutboxRetryPolicy, OutboxService


def test_outbox_service_enqueue_is_idempotent_by_key() -> None:
    existing = _job(idempotency_key="job-1")
    repository = FakeOutboxRepository(existing_by_key=existing)
    service = OutboxService(repository=repository)

    job = asyncio.run(service.enqueue(job_type="files.cleanup", payload={}, idempotency_key="job-1"))

    assert job is existing
    assert repository.created_job is None


def test_outbox_service_enqueue_creates_due_job() -> None:
    repository = FakeOutboxRepository()
    service = OutboxService(repository=repository)
    run_at = _now()

    job = asyncio.run(
        service.enqueue(
            job_type="files.cleanup",
            payload={"file_id": "file-1"},
            idempotency_key="job-1",
            run_at=run_at,
            request_id="req_123",
        )
    )

    assert job.job_type == "files.cleanup"
    assert job.payload == {"file_id": "file-1"}
    assert job.next_attempt_at == run_at
    assert job.request_id == "req_123"


def test_outbox_service_enqueue_rejects_blank_idempotency_key() -> None:
    service = OutboxService(repository=FakeOutboxRepository())

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.enqueue(job_type="files.cleanup", payload={}, idempotency_key="  "))

    assert exc_info.value.code == "validation_failed"


def test_outbox_service_enqueue_normalizes_idempotency_key() -> None:
    repository = FakeOutboxRepository()
    service = OutboxService(repository=repository)

    job = asyncio.run(service.enqueue(job_type="files.cleanup", payload={}, idempotency_key=" job-1 "))

    assert job.idempotency_key == "job-1"
    assert repository.lookup_key == "job-1"


def test_outbox_service_lock_next_due_sets_lease() -> None:
    repository = FakeOutboxRepository(lock_job=_job())
    service = OutboxService(repository=repository)
    now = _now()

    job = asyncio.run(service.lock_next_due(lease_seconds=45, now=now))

    assert job is repository.lock_job
    assert repository.lock_kwargs["now"] == now
    assert repository.lock_kwargs["locked_until"] == now + timedelta(seconds=45)


def test_outbox_retry_reschedules_with_backoff() -> None:
    repository = FakeOutboxRepository()
    service = OutboxService(
        repository=repository,
        retry_policy=OutboxRetryPolicy(base_delay_seconds=10, max_delay_seconds=60),
    )
    job = _job(attempts=2, max_attempts=3)
    now = _now()

    asyncio.run(service.mark_retryable_failure(job=job, error_code="provider_503", now=now))

    assert job.status == "queued"
    assert job.next_attempt_at == now + timedelta(seconds=20)
    assert job.error_code == "provider_503"


def test_outbox_retry_dead_letters_after_max_attempts() -> None:
    repository = FakeOutboxRepository()
    service = OutboxService(repository=repository)
    job = _job(attempts=3, max_attempts=3)

    asyncio.run(service.mark_retryable_failure(job=job, error_code="provider_503", now=_now()))

    assert job.status == "dead_lettered"
    assert job.error_code == "provider_503"


def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _job(
    *,
    job_type: str = "files.cleanup",
    idempotency_key: str = "job-1",
    attempts: int = 0,
    max_attempts: int = 3,
) -> OutboxJob:
    return OutboxJob(
        job_type=job_type,
        payload={},
        idempotency_key=idempotency_key,
        attempts=attempts,
        max_attempts=max_attempts,
        next_attempt_at=_now(),
    )


class FakeOutboxRepository:
    def __init__(self, *, existing_by_key=None, lock_job=None) -> None:
        self.existing_by_key = existing_by_key
        self.lock_job = lock_job
        self.created_job = None
        self.lock_kwargs = {}
        self.lookup_key = ""

    async def get_by_idempotency_key(self, *, idempotency_key: str):
        self.lookup_key = idempotency_key
        return self.existing_by_key

    async def create_job(self, **kwargs):
        self.created_job = OutboxJob(**kwargs)
        return self.created_job

    async def lock_next_due(self, **kwargs):
        self.lock_kwargs = kwargs
        return self.lock_job

    async def mark_completed(self, *, job):
        job.status = "completed"
        job.locked_until = None
        job.error_code = ""
        return job

    async def reschedule(self, *, job, next_attempt_at, error_code: str):
        job.status = "queued"
        job.locked_until = None
        job.next_attempt_at = next_attempt_at
        job.error_code = error_code
        return job

    async def dead_letter(self, *, job, error_code: str):
        job.status = "dead_lettered"
        job.locked_until = None
        job.error_code = error_code
        return job
