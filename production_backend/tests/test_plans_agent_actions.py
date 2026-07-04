import asyncio
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.plans.agent_actions import MILK_PLAN_CREATE_ACTION, MilkPlanCreateActionHandler
from production_backend.app.modules.plans.models import Plan
from production_backend.app.workers.errors import PermanentJobError


def test_milk_plan_create_action_handler_creates_plan_through_service() -> None:
    service = FakePlansService()
    action = _action(
        apply_payload={
            "title": "Increase pumping consistency",
            "summary": "Pump after morning and evening feeds.",
            "payload": {"target_sessions_per_day": 2},
        }
    )

    result = asyncio.run(MilkPlanCreateActionHandler(service=service)(action))

    assert result.resource_type == "plan"
    assert result.resource_id == str(service.plan.id)
    assert result.details == {
        "plan_type": "milk_management",
        "agent_action_id": str(action.id),
        "agent_run_id": str(action.run_id),
    }
    assert service.create_plan_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_plan_kwargs["plan_type"] == "milk_management"
    assert service.create_plan_kwargs["title"] == "Increase pumping consistency"
    assert service.create_plan_kwargs["summary"] == "Pump after morning and evening feeds."
    assert service.create_plan_kwargs["source"] == "agent_action"
    assert service.create_plan_kwargs["payload"]["target_sessions_per_day"] == 2
    assert service.create_plan_kwargs["payload"]["agent_action_id"] == str(action.id)
    assert service.create_plan_kwargs["idempotency_key"] == "idem-action"


def test_milk_plan_create_action_handler_rejects_missing_title() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(MilkPlanCreateActionHandler(service=FakePlansService())(_action(apply_payload={})))

    assert exc_info.value.code == "missing_plan_title"


class FakePlansService:
    def __init__(self) -> None:
        self.plan = Plan(
            id=uuid4(),
            owner_user_id=uuid4(),
            plan_type="milk_management",
            title="Increase pumping consistency",
            summary="Pump after morning and evening feeds.",
            source="agent_action",
            payload={},
        )
        self.create_plan_kwargs = {}

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
        self.plan.owner_user_id = kwargs["owner_user_id"]
        self.plan.plan_type = kwargs["plan_type"]
        self.plan.title = kwargs["title"]
        self.plan.summary = kwargs["summary"]
        self.plan.source = kwargs["source"]
        self.plan.payload = kwargs["payload"]
        return self.plan


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=MILK_PLAN_CREATE_ACTION,
        target_type="plan",
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
