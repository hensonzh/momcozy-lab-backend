import asyncio
from datetime import date
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.agent_runtime.tools import (
    ProfileReadToolHandler,
    SupportTicketProposeToolHandler,
    ToolHandlerContext,
    build_default_tool_handlers,
)
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile


def test_profile_read_tool_handler_returns_safe_context_projection() -> None:
    actor = _user()
    profile = UserProfile(
        user_id=actor.user_id,
        display_name="Mai",
        age=31,
        delivery_date=date(2026, 9, 20),
        lactation_advice="Hydrate",
        feeding_advice="Track feeds",
        daily_summary="Doing well",
    )
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=actor.user_id,
        infant_name="Nori",
        sex="female",
        birth_date=date(2026, 1, 10),
        status="active",
    )
    handler = ProfileReadToolHandler(service=FakeProfileService(profile=profile, infants=[infant]))

    result = asyncio.run(handler(_context(actor=actor, args={})))

    assert result["profile"]["user_id"] == str(actor.user_id)
    assert result["profile"]["delivery_date"] == "2026-09-20"
    assert result["profile"]["profile_onboarding_complete"] is True
    assert result["infants"] == [
        {
            "id": str(infant.id),
            "owner_user_id": str(actor.user_id),
            "infant_name": "Nori",
            "sex": "female",
            "birth_date": "2026-01-10",
            "status": "active",
        }
    ]


def test_support_ticket_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handler = SupportTicketProposeToolHandler(runtime_service=runtime_service)
    context = _context(
        actor=actor,
        args={
            "ticket": {
                "issue_type": "pump",
                "issue_summary": "Pump does not start",
                "product_model": "M9",
                "user_contact": "mai@example.com",
            },
            "locale": "en-US",
        },
    )

    result = asyncio.run(handler(context))

    assert result["action_id"] == str(runtime_service.action.id)
    assert result["action_type"] == "support.ticket.create"
    assert result["action_status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert result["preview_payload"]["has_user_contact"] is True
    assert "user_contact" not in result["preview_payload"]
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["apply_payload"]["user_contact"] == "mai@example.com"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"locale": "en-US"}


def test_support_ticket_propose_tool_handler_requires_summary() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(SupportTicketProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_build_default_tool_handlers_wires_registered_tool_names() -> None:
    handlers = build_default_tool_handlers(
        profile_service=FakeProfileService(profile=None, infants=[]),
        agent_runtime_service=FakeAgentRuntimeService(),
    )

    assert set(handlers) == {"profile.read", "support.ticket.propose"}


def _context(*, actor: CurrentUser | None = None, args: dict | None = None) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=actor or _user(),
        run_id=uuid4(),
        tool_name="tool",
        call_id="call-1",
        args=args or {},
    )


def _user() -> CurrentUser:
    user_id = uuid4()
    return CurrentUser(
        user_id=user_id,
        subject=str(user_id),
        session_id="session",
        token_id="token",
        roles=frozenset({"user"}),
        permissions=frozenset({"profile:read:self", "support_ticket:create:self"}),
    )


class FakeProfileService:
    def __init__(self, *, profile: UserProfile | None, infants: list[InfantProfile]) -> None:
        self.profile = profile
        self.infants = infants

    async def get_user_profile(self, *, user_id):
        return self.profile

    async def list_infants(self, *, owner_user_id):
        return self.infants


class FakeAgentRuntimeService:
    def __init__(self) -> None:
        self.calls = []
        self.action = AgentAction(
            id=uuid4(),
            run_id=uuid4(),
            actor_user_id=uuid4(),
            action_type="support.ticket.create",
            target_type="support_ticket",
            target_id="",
            status="confirmation_required",
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="",
            error_code="",
        )

    async def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        self.action.run_id = kwargs["run_id"]
        self.action.actor_user_id = kwargs["owner_user_id"]
        self.action.preview_payload = kwargs["preview_payload"]
        self.action.apply_payload = kwargs["apply_payload"]
        self.action.idempotency_key = kwargs["idempotency_key"]
        return self.action
