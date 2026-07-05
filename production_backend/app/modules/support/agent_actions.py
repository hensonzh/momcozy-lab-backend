from __future__ import annotations

from typing import Any

from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import SupportTicketsService


SUPPORT_TICKET_CREATE_ACTION = "support.ticket.create"


class SupportTicketCreateActionHandler:
    def __init__(self, *, service: SupportTicketsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        issue_summary = _text(payload, "issue_summary") or _text(payload, "summary")
        if not issue_summary:
            raise PermanentJobError("missing_issue_summary")

        ticket = await self.service.create_ticket(
            owner_user_id=action.actor_user_id,
            issue_type=_text(payload, "issue_type") or "other",
            issue_summary=issue_summary,
            product_model=_text(payload, "product_model"),
            order_number=_text(payload, "order_number"),
            purchase_channel=_text(payload, "purchase_channel"),
            user_contact=_text(payload, "user_contact"),
            urgency=_text(payload, "urgency") or "normal",
            source="agent_action",
            payload={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
                "raw_action_payload": payload,
            },
            request_id="",
            idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
        )
        return AgentActionApplyResult(
            resource_type="support_ticket",
            resource_id=str(ticket.id),
            details={"ticket_number": ticket.ticket_number},
        )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()
