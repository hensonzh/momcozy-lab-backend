import asyncio
from uuid import uuid4

import pytest

from app.modules.agent_runtime.models import AgentAction
from app.modules.support.agent_actions import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler
from app.modules.support.models import SupportTicket
from app.workers.errors import PermanentJobError


def test_support_ticket_create_action_handler_creates_ticket_through_service() -> None:
    service = FakeSupportTicketsService()
    action = _action(
        apply_payload={
            "issue_type": "device_fault",
            "issue_summary": "Pump does not turn on",
            "product_model": "Air1",
            "urgency": "high",
        }
    )

    result = asyncio.run(SupportTicketCreateActionHandler(service=service)(action))

    assert result.resource_type == "support_ticket"
    assert result.resource_id == str(service.ticket.id)
    assert result.details == {"ticket_number": "ticket_agent"}
    assert service.create_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_kwargs["issue_summary"] == "Pump does not turn on"
    assert service.create_kwargs["source"] == "agent_action"
    assert service.create_kwargs["idempotency_key"] == "idem-action"
    assert service.create_kwargs["payload"]["agent_action_id"] == str(action.id)


def test_support_ticket_create_action_handler_rejects_missing_summary() -> None:
    action = _action(apply_payload={})

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(SupportTicketCreateActionHandler(service=FakeSupportTicketsService())(action))

    assert exc_info.value.code == "missing_issue_summary"


class FakeSupportTicketsService:
    def __init__(self) -> None:
        self.ticket = SupportTicket(
            id=uuid4(),
            owner_user_id=uuid4(),
            ticket_number="ticket_agent",
            issue_type="device_fault",
            issue_summary="Pump does not turn on",
            product_model="Air1",
            order_number="",
            purchase_channel="",
            user_contact="",
            urgency="high",
            source="agent_action",
            payload={},
        )
        self.create_kwargs = {}

    async def create_ticket(self, **kwargs):
        self.create_kwargs = kwargs
        self.ticket.owner_user_id = kwargs["owner_user_id"]
        self.ticket.issue_type = kwargs["issue_type"]
        self.ticket.issue_summary = kwargs["issue_summary"]
        self.ticket.product_model = kwargs["product_model"]
        self.ticket.urgency = kwargs["urgency"]
        self.ticket.source = kwargs["source"]
        self.ticket.payload = kwargs["payload"]
        return self.ticket


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=SUPPORT_TICKET_CREATE_ACTION,
        target_type="support_ticket",
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
