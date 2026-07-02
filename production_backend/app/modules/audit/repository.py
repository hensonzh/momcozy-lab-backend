from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, IdempotencyKey


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_audit(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        details: dict[str, Any],
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
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
        return await self.session.scalar(statement)

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
