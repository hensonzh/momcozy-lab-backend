from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter

from ..core.metrics import RequestMetrics
from ..modules.audit.models import OutboxJob
from ..modules.audit.outbox import OutboxService
from .errors import PermanentJobError, RetryableJobError


OutboxHandler = Callable[[OutboxJob], Awaitable[None]]


class OutboxWorker:
    def __init__(
        self,
        *,
        service: OutboxService,
        handlers: Mapping[str, OutboxHandler],
        lease_seconds: int = 60,
        metrics: RequestMetrics | None = None,
    ) -> None:
        self.service = service
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        self.metrics = metrics

    async def run_once(self) -> bool:
        job = await self.service.lock_next_due(lease_seconds=self.lease_seconds)
        if job is None:
            return False

        started_at = perf_counter()
        handler = self.handlers.get(job.job_type)
        if handler is None:
            await self.service.mark_permanent_failure(job=job, error_code="handler_not_found")
            self._record(job=job, outcome="dead_lettered", error_code="handler_not_found", started_at=started_at)
            return True

        try:
            await handler(job)
        except RetryableJobError as exc:
            failed_job = await self.service.mark_retryable_failure(job=job, error_code=exc.code)
            outcome = "dead_lettered" if failed_job.status == "dead_lettered" else "retryable_failure"
            self._record(job=failed_job, outcome=outcome, error_code=exc.code, started_at=started_at)
        except PermanentJobError as exc:
            await self.service.mark_permanent_failure(job=job, error_code=exc.code)
            self._record(job=job, outcome="dead_lettered", error_code=exc.code, started_at=started_at)
        except Exception:
            failed_job = await self.service.mark_retryable_failure(job=job, error_code="handler_error")
            outcome = "dead_lettered" if failed_job.status == "dead_lettered" else "retryable_failure"
            self._record(job=failed_job, outcome=outcome, error_code="handler_error", started_at=started_at)
        else:
            await self.service.mark_completed(job=job)
            self._record(job=job, outcome="completed", error_code="", started_at=started_at)

        return True

    def _record(self, *, job: OutboxJob, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_worker_job(
                job_type=job.job_type,
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )
