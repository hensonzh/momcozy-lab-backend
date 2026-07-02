from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

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
    ) -> None:
        self.service = service
        self.handlers = handlers
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        job = await self.service.lock_next_due(lease_seconds=self.lease_seconds)
        if job is None:
            return False

        handler = self.handlers.get(job.job_type)
        if handler is None:
            await self.service.mark_permanent_failure(job=job, error_code="handler_not_found")
            return True

        try:
            await handler(job)
        except RetryableJobError as exc:
            await self.service.mark_retryable_failure(job=job, error_code=exc.code)
        except PermanentJobError as exc:
            await self.service.mark_permanent_failure(job=job, error_code=exc.code)
        except Exception:
            await self.service.mark_retryable_failure(job=job, error_code="handler_error")
        else:
            await self.service.mark_completed(job=job)

        return True
