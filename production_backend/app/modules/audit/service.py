from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from .models import AuditLog, IdempotencyKey
from .repository import AuditRepository


@dataclass(frozen=True)
class IdempotencyDecision:
    status: str
    record: IdempotencyKey


class AuditService:
    def __init__(self, *, repository: AuditRepository) -> None:
        self.repository = repository

    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        actor_type: str | None = None,
        actor_service: str = "",
        action: str,
        resource_type: str,
        resource_id: str = "",
        request_id: str = "",
        outcome: str = "succeeded",
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repository.record_audit(
            actor_user_id=actor_user_id,
            actor_type=actor_type or ("user" if actor_user_id is not None else "system"),
            actor_service=actor_service.strip(),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            details=details or {},
        )


class IdempotencyService:
    def __init__(self, *, repository: AuditRepository) -> None:
        self.repository = repository

    async def reserve(
        self,
        *,
        actor_user_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyDecision:
        existing = await self.repository.get_idempotency_key(
            actor_user_id=actor_user_id,
            scope=scope,
            key=key,
        )
        if existing is None:
            created = await self.repository.create_idempotency_key(
                actor_user_id=actor_user_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            return IdempotencyDecision(status="reserved", record=created)

        if existing.request_hash != request_hash:
            raise ApiError(code="idempotency_conflict", message="Idempotency key was reused with a different request.", status=409)

        return IdempotencyDecision(status="replay", record=existing)

    async def mark_completed(self, *, record: IdempotencyKey, response_ref: str) -> IdempotencyKey:
        return await self.repository.mark_idempotency_completed(idempotency_key=record, response_ref=response_ref)


def request_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
