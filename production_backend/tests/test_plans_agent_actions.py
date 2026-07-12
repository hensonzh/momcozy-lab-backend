import asyncio
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    MilkPlanCreateActionHandler,
    PlanDeleteActionHandler,
    PlanTaskCompleteActionHandler,
    PlanTaskCreateActionHandler,
    PlanTaskDeleteActionHandler,
    PlanTaskUpdateActionHandler,
    PregnancyPlanCreateActionHandler,
)
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.workers.errors import PermanentJobError


PRIVATE_PREGNANCY_PLAN_CONTENT = "private thyroid medication and birth plan card"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"


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
    assert result.application_events == ()


def test_milk_plan_create_action_handler_rejects_missing_title() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(MilkPlanCreateActionHandler(service=FakePlansService())(_action(apply_payload={})))

    assert exc_info.value.code == "missing_plan_title"


def test_pregnancy_plan_create_action_handler_creates_plan_through_service() -> None:
    service = FakePlansService()
    action = _action(
        action_type=PREGNANCY_PLAN_CREATE_ACTION,
        target_type="plan",
        apply_payload={
            "title": "Third trimester plan",
            "summary": "Prepare appointments and bag tasks.",
            "payload": {
                "gestational_week": 32,
                "plan_context": {"medical_notes": PRIVATE_PREGNANCY_PLAN_CONTENT},
                "card": {"card_json": {"title": PRIVATE_PREGNANCY_PLAN_CONTENT}},
            },
        },
    )

    result = asyncio.run(PregnancyPlanCreateActionHandler(service=service)(action))

    assert result.resource_type == "plan"
    assert result.resource_id == str(service.plan.id)
    assert result.details["plan_type"] == "pregnancy"
    assert service.create_plan_kwargs["plan_type"] == "pregnancy"
    assert service.create_plan_kwargs["source"] == "agent_action"
    assert service.create_plan_kwargs["payload"]["gestational_week"] == 32
    assert service.create_plan_kwargs["payload"]["agent_action_id"] == str(action.id)
    assert len(result.application_events) == 1
    changed_event = result.application_events[0]
    assert changed_event.event_type == PREGNANCY_PLAN_CHANGED_EVENT
    assert changed_event.payload == {
        "operation": "created",
        "plan_id": str(service.plan.id),
        "plan_type": "pregnancy",
        "source": "agent_action",
    }


def test_plan_task_create_action_handler_creates_task_through_service() -> None:
    service = FakePlansService()
    plan_id = uuid4()
    action = _action(
        action_type=PLAN_TASK_CREATE_ACTION,
        target_type="plan_task",
        apply_payload={
            "plan_id": str(plan_id),
            "task_date": "2026-07-04",
            "task_time": "09:00",
            "title": "Book prenatal appointment",
            "description": "Ask about birth plan questions.",
            "payload": {"category": "appointments"},
        },
    )

    result = asyncio.run(PlanTaskCreateActionHandler(service=service)(action))

    assert result.resource_type == "plan_task"
    assert result.resource_id == str(service.task.id)
    assert result.details["status"] == "pending"
    assert service.create_task_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_task_kwargs["plan_id"] == plan_id
    assert service.create_task_kwargs["task_date"].isoformat() == "2026-07-04"
    assert service.create_task_kwargs["title"] == "Book prenatal appointment"
    assert service.create_task_kwargs["payload"]["category"] == "appointments"
    assert service.create_task_kwargs["payload"]["agent_action_id"] == str(action.id)
    assert service.create_task_kwargs["idempotency_key"] == "idem-action"


def test_plan_task_complete_action_handler_sets_completion_through_service() -> None:
    service = FakePlansService()
    task_id = uuid4()
    action = _action(
        action_type=PLAN_TASK_COMPLETE_ACTION,
        target_type="plan_task",
        apply_payload={"task_id": str(task_id), "completed": False},
    )

    result = asyncio.run(PlanTaskCompleteActionHandler(service=service)(action))

    assert result.resource_type == "plan_task"
    assert result.resource_id == str(service.task.id)
    assert result.details["completed"] is False
    assert service.set_task_completed_kwargs["owner_user_id"] == action.actor_user_id
    assert service.set_task_completed_kwargs["task_id"] == task_id
    assert service.set_task_completed_kwargs["completed"] is False


def test_plan_task_update_action_handler_updates_task_through_service() -> None:
    service = FakePlansService()
    task_id = uuid4()
    plan_id = uuid4()
    action = _action(
        action_type=PLAN_TASK_UPDATE_ACTION,
        target_type="plan_task",
        apply_payload={
            "task_id": str(task_id),
            "plan_id": str(plan_id),
            "task_date": "2026-07-05",
            "task_time": "10:30",
            "title": "Move pumping session",
            "payload": {"reason": "meeting"},
        },
    )

    result = asyncio.run(PlanTaskUpdateActionHandler(service=service)(action))

    assert result.resource_type == "plan_task"
    assert result.resource_id == str(service.task.id)
    assert result.details["fields"] == ["payload", "plan_id", "task_date", "task_time", "title"]
    assert service.update_task_kwargs["owner_user_id"] == action.actor_user_id
    assert service.update_task_kwargs["task_id"] == task_id
    assert service.update_task_kwargs["updates"]["plan_id"] == plan_id
    assert service.update_task_kwargs["updates"]["task_date"].isoformat() == "2026-07-05"
    assert service.update_task_kwargs["updates"]["title"] == "Move pumping session"


def test_plan_task_delete_action_handler_deletes_task_through_service() -> None:
    service = FakePlansService()
    task_id = uuid4()
    action = _action(
        action_type=PLAN_TASK_DELETE_ACTION,
        target_type="plan_task",
        apply_payload={"task_id": str(task_id)},
    )

    result = asyncio.run(PlanTaskDeleteActionHandler(service=service)(action))

    assert result.resource_type == "plan_task"
    assert result.resource_id == str(task_id)
    assert service.delete_task_kwargs["owner_user_id"] == action.actor_user_id
    assert service.delete_task_kwargs["task_id"] == task_id


def test_plan_delete_action_handler_deletes_plan_through_service() -> None:
    service = FakePlansService()
    plan_id = uuid4()
    action = _action(
        action_type=PLAN_DELETE_ACTION,
        target_type="plan",
        apply_payload={"plan_id": str(plan_id)},
    )

    result = asyncio.run(PlanDeleteActionHandler(service=service)(action))

    assert result.resource_type == "plan"
    assert result.resource_id == str(plan_id)
    assert service.delete_plan_kwargs["owner_user_id"] == action.actor_user_id
    assert service.delete_plan_kwargs["plan_id"] == plan_id


def test_plan_task_create_action_handler_rejects_missing_title() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            PlanTaskCreateActionHandler(service=FakePlansService())(
                _action(action_type=PLAN_TASK_CREATE_ACTION, target_type="plan_task", apply_payload={})
            )
        )

    assert exc_info.value.code == "missing_task_title"


def test_plan_task_complete_action_handler_rejects_missing_task_id() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            PlanTaskCompleteActionHandler(service=FakePlansService())(
                _action(action_type=PLAN_TASK_COMPLETE_ACTION, target_type="plan_task", apply_payload={})
            )
        )

    assert exc_info.value.code == "missing_task_id"


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
        self.task = PlanTask(
            id=uuid4(),
            owner_user_id=uuid4(),
            plan_id=None,
            title="Book prenatal appointment",
            status="pending",
            payload={},
        )
        self.create_plan_kwargs = {}
        self.create_task_kwargs = {}
        self.set_task_completed_kwargs = {}
        self.update_task_kwargs = {}
        self.delete_task_kwargs = {}
        self.delete_plan_kwargs = {}

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
        self.plan.owner_user_id = kwargs["owner_user_id"]
        self.plan.plan_type = kwargs["plan_type"]
        self.plan.title = kwargs["title"]
        self.plan.summary = kwargs["summary"]
        self.plan.source = kwargs["source"]
        self.plan.payload = kwargs["payload"]
        return self.plan

    async def create_task(self, **kwargs):
        self.create_task_kwargs = kwargs
        self.task.owner_user_id = kwargs["owner_user_id"]
        self.task.plan_id = kwargs["plan_id"]
        self.task.task_date = kwargs["task_date"]
        self.task.task_time = kwargs["task_time"]
        self.task.title = kwargs["title"]
        self.task.description = kwargs["description"]
        self.task.payload = kwargs["payload"]
        return self.task

    async def set_task_completed(self, **kwargs):
        self.set_task_completed_kwargs = kwargs
        self.task.owner_user_id = kwargs["owner_user_id"]
        self.task.id = kwargs["task_id"]
        self.task.status = "completed" if kwargs["completed"] else "pending"
        return self.task

    async def update_task(self, **kwargs):
        self.update_task_kwargs = kwargs
        for key, value in kwargs["updates"].items():
            setattr(self.task, key, value)
        return self.task

    async def delete_task(self, **kwargs):
        self.delete_task_kwargs = kwargs

    async def delete_plan(self, **kwargs):
        self.delete_plan_kwargs = kwargs


def _action(*, apply_payload: dict, action_type: str = MILK_PLAN_CREATE_ACTION, target_type: str = "plan") -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=action_type,
        target_type=target_type,
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
