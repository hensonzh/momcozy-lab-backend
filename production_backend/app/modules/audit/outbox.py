from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from .models import OutboxJob
from .repository import OutboxRepository


@dataclass(frozen=True)
class OutboxRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: int = 30
    max_delay_seconds: int = 900


class OutboxService:
    def __init__(self, *, repository: OutboxRepository, retry_policy: OutboxRetryPolicy | None = None) -> None:
        self.repository = repository
        self.retry_policy = retry_policy or OutboxRetryPolicy()

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        action_id: UUID | None = None,
        max_attempts: int | None = None,
        run_at: datetime | None = None,
        request_id: str = "",
        trace_id: str = "",
    ) -> OutboxJob:
        normalized_key = idempotency_key.strip()
        if not normalized_key:
            raise ApiError(code="validation_failed", message="Outbox idempotency key is required.", status=422)

        existing = await self.repository.get_by_idempotency_key(idempotency_key=normalized_key)
        if existing is not None:
            if action_id is not None and existing.action_id != action_id:
                raise ApiError(
                    code="idempotency_conflict",
                    message="Outbox idempotency key is already associated with a different action.",
                    status=409,
                )
            return existing

        return await self.repository.create_job(
            job_type=job_type,
            payload=payload,
            idempotency_key=normalized_key,
            action_id=action_id,
            max_attempts=max_attempts or self.retry_policy.max_attempts,
            next_attempt_at=run_at or _utcnow(),
            request_id=request_id,
            trace_id=trace_id,
        )

    async def lock_next_due(self, *, lease_seconds: int, now: datetime | None = None) -> OutboxJob | None:
        timestamp = now or _utcnow()
        return await self.repository.lock_next_due(
            now=timestamp,
            locked_until=timestamp + timedelta(seconds=lease_seconds),
        )

    async def mark_completed(self, *, job: OutboxJob) -> OutboxJob:
        return await self.repository.mark_completed(job=job)

    async def mark_retryable_failure(
        self,
        *,
        job: OutboxJob,
        error_code: str,
        now: datetime | None = None,
    ) -> OutboxJob:
        if job.attempts >= job.max_attempts:
            return await self.repository.dead_letter(job=job, error_code=error_code)

        timestamp = now or _utcnow()
        delay = self._retry_delay_seconds(job.attempts)
        return await self.repository.reschedule(
            job=job,
            next_attempt_at=timestamp + timedelta(seconds=delay),
            error_code=error_code,
        )

    async def mark_permanent_failure(self, *, job: OutboxJob, error_code: str) -> OutboxJob:
        return await self.repository.dead_letter(job=job, error_code=error_code)

    def _retry_delay_seconds(self, attempts: int) -> int:
        exponent = max(attempts - 1, 0)
        delay = self.retry_policy.base_delay_seconds * (2**exponent)
        capped_delay: int = min(delay, self.retry_policy.max_delay_seconds)
        return capped_delay


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
