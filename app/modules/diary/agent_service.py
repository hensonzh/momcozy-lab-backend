from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import request_hash
from .agent_contracts import AgentDiaryApplyPayload
from .events import PREGNANCY_DIARY_CHANGED_EVENT, pregnancy_diary_changed_payload


AGENT_DIARY_IDEMPOTENCY_SCOPE = "internal.agent.pregnancy_diary.entry"
AGENT_DIARY_ACTION_TYPES = {
    "create": "pregnancy_diary.entry.save",
    "update": "pregnancy_diary.entry.save",
    "delete": "pregnancy_diary.entry.delete",
}
APPLIED_OPERATIONS = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
}


@dataclass(frozen=True)
class AgentDiaryWriteResult:
    resource_type: str
    resource_id: str
    details: dict[str, Any]
    application_events: tuple[dict[str, Any], ...]


class AgentDiaryWriteService:
    """Product-owned, action-bound pregnancy diary mutation boundary."""

    def __init__(
        self,
        *,
        diary_service: Any,
        idempotency_service: Any,
        audit_service: Any | None = None,
    ) -> None:
        self.diary_service = diary_service
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply_idempotent(
        self,
        *,
        owner_user_id: UUID,
        payload: AgentDiaryApplyPayload | dict[str, Any],
        idempotency_key: str,
        action_id: UUID,
        run_id: UUID,
        actor_service: str,
        request_id: str,
    ) -> AgentDiaryWriteResult:
        command = (
            payload
            if isinstance(payload, AgentDiaryApplyPayload)
            else AgentDiaryApplyPayload.model_validate(payload)
        )
        expected_key = f"agent-action:{action_id}"
        if idempotency_key != expected_key:
            raise ApiError(
                code="validation_failed",
                message="Idempotency-Key must be bound to action_id.",
                status=422,
            )
        normalized_actor_service = actor_service.strip()
        if not normalized_actor_service:
            raise ApiError(
                code="validation_failed",
                message="Service actor is required.",
                status=422,
            )

        action_type = AGENT_DIARY_ACTION_TYPES[command.operation]
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=AGENT_DIARY_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_hash=request_hash(
                {
                    "actor": {
                        "type": "service",
                        "service": normalized_actor_service,
                        "user_id": str(owner_user_id),
                    },
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                    "action_type": action_type,
                    "payload": command.model_dump(mode="json", exclude_unset=True),
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            return _replay_result(
                response_ref=decision.record.response_ref,
                command=command,
            )

        entry, changed = await self._apply(
            owner_user_id=owner_user_id,
            command=command,
            request_id=request_id,
        )
        applied_operation = APPLIED_OPERATIONS[command.operation]
        details = {
            "operation": applied_operation,
            "changed": changed,
        }
        events = _application_events(
            entry=entry,
            operation=applied_operation,
            changed=changed,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=action_type,
                resource_type="pregnancy_diary_entry",
                resource_id=str(entry.id),
                request_id=request_id,
                details={
                    **details,
                    "owner_user_id": str(owner_user_id),
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=_response_ref(
                resource_id=entry.id,
                updated_at=entry.updated_at,
            ),
        )
        return AgentDiaryWriteResult(
            resource_type="pregnancy_diary_entry",
            resource_id=str(entry.id),
            details=details,
            application_events=events,
        )

    async def _apply(
        self,
        *,
        owner_user_id: UUID,
        command: AgentDiaryApplyPayload,
        request_id: str,
    ) -> tuple[Any, bool]:
        if command.operation == "create":
            entry = await self.diary_service.create_entry(
                owner_user_id=owner_user_id,
                entry_date=command.entry_date,
                values={"content": command.content},
                request_id=request_id,
            )
            return entry, True
        if command.operation == "update":
            mutation = await self.diary_service.update_entry_with_status(
                owner_user_id=owner_user_id,
                entry_date=command.entry_date,
                values={"content": command.content},
                request_id=request_id,
            )
            return mutation.entry, bool(mutation.changed)
        entry = await self.diary_service.delete_entry(
            owner_user_id=owner_user_id,
            entry_date=command.entry_date,
            request_id=request_id,
        )
        return entry, True


def _application_events(
    *,
    entry: Any,
    operation: str,
    changed: bool,
) -> tuple[dict[str, Any], ...]:
    if not changed:
        return ()
    return (
        {
            "type": PREGNANCY_DIARY_CHANGED_EVENT,
            "payload": pregnancy_diary_changed_payload(
                entry=entry,
                operation=operation,
                source="agent_action",
            ),
        },
    )


def _response_ref(*, resource_id: UUID, updated_at: datetime) -> str:
    normalized_updated_at = updated_at
    if normalized_updated_at.tzinfo is None:
        normalized_updated_at = normalized_updated_at.replace(tzinfo=timezone.utc)
    return json.dumps(
        {
            "resource_id": str(resource_id),
            "updated_at": normalized_updated_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _replay_result(
    *,
    response_ref: str,
    command: AgentDiaryApplyPayload,
) -> AgentDiaryWriteResult:
    try:
        parsed = json.loads(response_ref)
        resource_id = str(parsed["resource_id"])
        UUID(resource_id)
        updated_at = datetime.fromisoformat(str(parsed["updated_at"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError(
            code="conflict",
            message="Diary action replay identity is invalid.",
            status=409,
        ) from exc
    operation = APPLIED_OPERATIONS[command.operation]
    return AgentDiaryWriteResult(
        resource_type="pregnancy_diary_entry",
        resource_id=resource_id,
        details={"operation": operation, "changed": True},
        application_events=(
            {
                "type": PREGNANCY_DIARY_CHANGED_EVENT,
                "payload": {
                    "operation": operation,
                    "entry_id": resource_id,
                    "entry_date": command.entry_date.isoformat(),
                    "updated_at": updated_at.isoformat(),
                    "source": "agent_action",
                },
            },
        ),
    )
