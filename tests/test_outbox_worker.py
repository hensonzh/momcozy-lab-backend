import asyncio

from app.modules.audit.models import OutboxJob
from app.core.metrics import RequestMetrics
from app.workers.outbox import OutboxWorker, PermanentJobError, RetryableJobError


def test_outbox_worker_returns_false_when_no_job() -> None:
    service = FakeOutboxService(job=None)
    worker = OutboxWorker(service=service, handlers={})

    assert asyncio.run(worker.run_once()) is False


def test_outbox_worker_marks_completed_after_handler_success() -> None:
    job = _job()
    service = FakeOutboxService(job=job)

    async def handler(handled_job):
        assert handled_job is job

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler})

    assert asyncio.run(worker.run_once()) is True
    assert service.completed_job is job


def test_outbox_worker_reschedules_retryable_error() -> None:
    job = _job()
    service = FakeOutboxService(job=job)

    async def handler(_job):
        raise RetryableJobError("provider_503")

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler})

    assert asyncio.run(worker.run_once()) is True
    assert service.retryable_error_code == "provider_503"


def test_outbox_worker_dead_letters_permanent_error() -> None:
    job = _job()
    service = FakeOutboxService(job=job)

    async def handler(_job):
        raise PermanentJobError("invalid_payload")

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler})

    assert asyncio.run(worker.run_once()) is True
    assert service.permanent_error_code == "invalid_payload"


def test_outbox_worker_dead_letters_missing_handler() -> None:
    job = _job(job_type="unknown")
    service = FakeOutboxService(job=job)
    worker = OutboxWorker(service=service, handlers={})

    assert asyncio.run(worker.run_once()) is True
    assert service.permanent_error_code == "handler_not_found"


def test_outbox_worker_treats_unexpected_error_as_retryable() -> None:
    job = _job()
    service = FakeOutboxService(job=job)

    async def handler(_job):
        raise RuntimeError("boom")

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler})

    assert asyncio.run(worker.run_once()) is True
    assert service.retryable_error_code == "handler_error"


def test_outbox_worker_records_job_metrics() -> None:
    job = _job()
    service = FakeOutboxService(job=job)
    metrics = RequestMetrics()

    async def handler(_job):
        raise RetryableJobError("provider_503")

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler}, metrics=metrics)

    assert asyncio.run(worker.run_once()) is True
    worker_metrics = metrics.snapshot()["workers"][0]
    assert worker_metrics["job_type"] == "files.cleanup"
    assert worker_metrics["outcome_counts"]["retryable_failure"] == 1
    assert worker_metrics["error_code_counts"]["provider_503"] == 1


def test_outbox_worker_records_retry_exhaustion_as_dead_lettered() -> None:
    job = _job()
    service = FakeOutboxService(job=job, retry_exhausted=True)
    metrics = RequestMetrics()

    async def handler(_job):
        raise RetryableJobError("provider_503")

    worker = OutboxWorker(service=service, handlers={"files.cleanup": handler}, metrics=metrics)

    assert asyncio.run(worker.run_once()) is True
    worker_metrics = metrics.snapshot()["workers"][0]
    assert service.retryable_error_code == "provider_503"
    assert worker_metrics["outcome_counts"]["dead_lettered"] == 1
    assert worker_metrics["error_code_counts"]["provider_503"] == 1


def _job(*, job_type: str = "files.cleanup") -> OutboxJob:
    return OutboxJob(job_type=job_type, payload={}, idempotency_key="job-1")


class FakeOutboxService:
    def __init__(self, *, job, retry_exhausted: bool = False) -> None:
        self.job = job
        self.retry_exhausted = retry_exhausted
        self.completed_job = None
        self.retryable_error_code = ""
        self.permanent_error_code = ""

    async def lock_next_due(self, *, lease_seconds: int):
        return self.job

    async def mark_completed(self, *, job):
        self.completed_job = job

    async def mark_retryable_failure(self, *, job, error_code: str):
        self.retryable_error_code = error_code
        if self.retry_exhausted:
            job.status = "dead_lettered"
        return job

    async def mark_permanent_failure(self, *, job, error_code: str):
        self.permanent_error_code = error_code
        job.status = "dead_lettered"
        return job
