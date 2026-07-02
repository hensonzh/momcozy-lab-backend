from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, IdempotencyKey, OutboxJob


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_audit(
        self,
        *,
        actor_user_id: UUID | None,
        actor_type: str,
        actor_service: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        details: dict[str, Any],
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            actor_service=actor_service,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            details=details,
        )
        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def get_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
    ) -> IdempotencyKey | None:
        statement = select(IdempotencyKey).where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == key,
        )
        return cast(IdempotencyKey | None, await self.session.scalar(statement))

    async def create_idempotency_key(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyKey:
        idempotency_key = IdempotencyKey(
            actor_user_id=actor_user_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            expires_at=expires_at,
        )
        self.session.add(idempotency_key)
        await self.session.flush()
        return idempotency_key

    async def mark_idempotency_completed(
        self,
        *,
        idempotency_key: IdempotencyKey,
        response_ref: str,
    ) -> IdempotencyKey:
        idempotency_key.status = "completed"
        idempotency_key.response_ref = response_ref
        await self.session.flush()
        return idempotency_key


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_idempotency_key(self, *, idempotency_key: str) -> OutboxJob | None:
        statement = select(OutboxJob).where(OutboxJob.idempotency_key == idempotency_key)
        return cast(OutboxJob | None, await self.session.scalar(statement))

    async def create_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        action_id: UUID | None,
        max_attempts: int,
        next_attempt_at: datetime,
        request_id: str,
        trace_id: str,
    ) -> OutboxJob:
        job = OutboxJob(
            action_id=action_id,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            next_attempt_at=next_attempt_at,
            request_id=request_id,
            trace_id=trace_id,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def lock_next_due(
        self,
        *,
        now: datetime,
        locked_until: datetime,
    ) -> OutboxJob | None:
        statement = (
            select(OutboxJob)
            .where(
                or_(
                    and_(OutboxJob.status == "queued", OutboxJob.next_attempt_at <= now),
                    and_(OutboxJob.status == "locked", OutboxJob.locked_until <= now),
                )
            )
            .order_by(OutboxJob.next_attempt_at, OutboxJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = cast(OutboxJob | None, await self.session.scalar(statement))
        if job is None:
            return None

        job.status = "locked"
        job.locked_until = locked_until
        job.attempts += 1
        await self.session.flush()
        return job

    async def mark_completed(self, *, job: OutboxJob) -> OutboxJob:
        job.status = "completed"
        job.locked_until = None
        job.error_code = ""
        await self.session.flush()
        return job

    async def reschedule(
        self,
        *,
        job: OutboxJob,
        next_attempt_at: datetime,
        error_code: str,
    ) -> OutboxJob:
        job.status = "queued"
        job.locked_until = None
        job.next_attempt_at = next_attempt_at
        job.error_code = error_code
        await self.session.flush()
        return job

    async def dead_letter(self, *, job: OutboxJob, error_code: str) -> OutboxJob:
        job.status = "dead_lettered"
        job.locked_until = None
        job.error_code = error_code
        await self.session.flush()
        return job
