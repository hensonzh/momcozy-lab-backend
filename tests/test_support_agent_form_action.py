import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.agent_runtime.runs.models import AgentArtifact, AgentRun
from app.modules.auth import CurrentUser
from app.modules.support.models import SupportTicket
from app.agents.cozymate.actions.support_form import create_agent_form_ticket
from app.modules.support.schemas import SupportTicketCreate
from tests.test_agent_runtime_actions import FakeActionRepository


def test_agent_form_submission_applies_support_action_without_second_confirmation(monkeypatch) -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    repository = _runtime_repository(owner_user_id=owner_user_id, thread_id=thread_id)
    service = FakeSupportService(owner_user_id=owner_user_id)
    monkeypatch.setattr("app.agents.cozymate.actions.support_form.AgentRuntimeRepository", lambda _session: repository)

    ticket = asyncio.run(
        create_agent_form_ticket(
            payload=SupportTicketCreate(
                issue_type="device_fault",
                issue_summary="Pump does not start",
                product_model="Air1",
                source="agent_form",
                payload={"artifact_id": str(repository.artifact.id)},
                thread_id=str(thread_id),
            ),
            current_user=_user(owner_user_id),
            service=service,
            idempotency_key="support-form-1",
        )
    )

    assert ticket.id == service.ticket.id
    assert len(repository.actions) == 1
    assert repository.actions[0].action_type == "support.ticket.create"
    assert repository.actions[0].status == "applied"
    assert service.create_kwargs["source"] == "agent_action"
    assert [event.event_type for event in repository.events][-1] == "action.applied"
    assert "action.confirmation_required" not in [event.event_type for event in repository.events]


def test_agent_form_submission_rejects_thread_mismatch_before_action(monkeypatch) -> None:
    owner_user_id = uuid4()
    repository = _runtime_repository(owner_user_id=owner_user_id, thread_id=uuid4())
    service = FakeSupportService(owner_user_id=owner_user_id)
    monkeypatch.setattr("app.agents.cozymate.actions.support_form.AgentRuntimeRepository", lambda _session: repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_agent_form_ticket(
                payload=SupportTicketCreate(
                    issue_summary="Pump does not start",
                    source="agent_form",
                    payload={"artifact_id": str(repository.artifact.id)},
                    thread_id=str(uuid4()),
                ),
                current_user=_user(owner_user_id),
                service=service,
                idempotency_key="support-form-2",
            )
        )

    assert exc_info.value.code == "support_ticket_thread_mismatch"
    assert repository.actions == []


class FakeSupportService:
    def __init__(self, *, owner_user_id) -> None:
        self.repository = SimpleNamespace(session=object())
        self.ticket = SupportTicket(
            id=uuid4(),
            owner_user_id=owner_user_id,
            ticket_number="ticket_test",
            status="submitted",
            issue_type="device_fault",
            issue_summary="Pump does not start",
            product_model="Air1",
            order_number="",
            purchase_channel="",
            user_contact="",
            urgency="normal",
            source="agent_action",
            payload={},
        )
        self.create_kwargs = {}

    async def create_ticket(self, **kwargs):
        self.create_kwargs = kwargs
        return self.ticket

    async def get_ticket(self, **_kwargs):
        return self.ticket


def _runtime_repository(*, owner_user_id, thread_id) -> FakeActionRepository:
    repository = FakeActionRepository()
    repository.run = AgentRun(
        id=uuid4(),
        thread_id=thread_id,
        actor_user_id=owner_user_id,
        status="running",
        runtime_pattern="sdk_only",
        runtime_version="momcozy-agent-v1",
        prompt_version="",
        request_id="req",
        trace_id="trace",
        error_code="",
        error_details={},
    )
    repository.runs = [repository.run]
    repository.artifact = AgentArtifact(
        id=uuid4(),
        run_id=repository.run.id,
        owner_user_id=owner_user_id,
        artifact_type="support_ticket_draft",
        schema_version="1.0",
        status="created",
        payload={},
        raw_payload_ref="",
    )
    return repository


def _user(user_id) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        subject=str(user_id),
        session_id="session",
        token_id="token",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
