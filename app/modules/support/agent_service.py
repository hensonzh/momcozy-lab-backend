from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, request_hash
from .agent_contracts import AgentSupportTicketPayload
from .repository import SupportTicketsRepository
from .service import SupportTicketsService


AGENT_SUPPORT_TICKET_ACTION_TYPE = "support.ticket.create"
AGENT_SUPPORT_TICKET_IDEMPOTENCY_SCOPE = "internal.agent.support.ticket"


@dataclass(frozen=True)
class AgentSupportTicketActionResult:
    resource_type: str
    resource_id: str
    details: dict[str, str]
    application_events: tuple[dict[str, Any], ...]


class AgentSupportTicketActionService:
    """Product-owned, action-bound support ticket submission boundary."""

    def __init__(
        self,
        *,
        repository: SupportTicketsRepository,
        idempotency_service: IdempotencyService,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply_idempotent(
        self,
        *,
        owner_user_id: UUID,
        payload: AgentSupportTicketPayload | dict[str, Any],
        idempotency_key: str,
        action_id: UUID,
        run_id: UUID,
        actor_service: str,
        request_id: str,
    ) -> AgentSupportTicketActionResult:
        command = (
            payload
            if isinstance(payload, AgentSupportTicketPayload)
            else AgentSupportTicketPayload.model_validate(payload)
        )
        if idempotency_key != f"agent-action:{action_id}":
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
        command_payload = command.model_dump(mode="json", exclude_unset=True)
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=AGENT_SUPPORT_TICKET_IDEMPOTENCY_SCOPE,
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
                    "action_type": AGENT_SUPPORT_TICKET_ACTION_TYPE,
                    "payload": command_payload,
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            return _replay_result(decision.record.response_ref)

        ticket = await SupportTicketsService(
            repository=self.repository
        ).create_ticket(
            owner_user_id=owner_user_id,
            issue_type=command.issue_type,
            issue_summary=command.issue_summary,
            product_model=command.product_model,
            order_number=command.order_number,
            purchase_channel=command.purchase_channel,
            user_contact=command.user_contact,
            urgency=command.urgency,
            source="agent_action",
            payload={
                "agent_action_id": str(action_id),
                "agent_run_id": str(run_id),
                "troubleshooting_done": command.troubleshooting_done,
                "user_emotion": command.user_emotion,
                "attachments_note": command.attachments_note,
                "locale": command.locale,
            },
            request_id=request_id,
        )
        result = AgentSupportTicketActionResult(
            resource_type="support_ticket",
            resource_id=str(ticket.id),
            details={
                "ticket_number": ticket.ticket_number,
                "status": ticket.status,
            },
            application_events=(
                {
                    "type": "support.ticket.submitted",
                    "payload": {
                        "ticket_id": str(ticket.id),
                        "ticket_number": ticket.ticket_number,
                        "status": ticket.status,
                        "source": "agent_action",
                    },
                },
            ),
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=AGENT_SUPPORT_TICKET_ACTION_TYPE,
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                request_id=request_id,
                details={
                    **result.details,
                    "owner_user_id": str(owner_user_id),
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=_response_ref(result),
        )
        return result


def _response_ref(result: AgentSupportTicketActionResult) -> str:
    return json.dumps(
        {
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "details": result.details,
            "application_events": list(result.application_events),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _replay_result(response_ref: str) -> AgentSupportTicketActionResult:
    if not response_ref:
        raise ApiError(
            code="idempotency_in_progress",
            message="Support ticket action is still in progress.",
            status=409,
        )
    try:
        parsed = json.loads(response_ref)
        resource_id = str(parsed["resource_id"])
        UUID(resource_id)
        if parsed["resource_type"] != "support_ticket":
            raise ValueError
        details = {
            str(key): str(value)
            for key, value in dict(parsed["details"]).items()
        }
        application_events = tuple(
            dict(event) for event in parsed["application_events"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError(
            code="conflict",
            message="Support ticket action replay identity is invalid.",
            status=409,
        ) from exc
    return AgentSupportTicketActionResult(
        resource_type="support_ticket",
        resource_id=resource_id,
        details=details,
        application_events=application_events,
    )
