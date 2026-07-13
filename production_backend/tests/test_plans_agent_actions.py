import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    MILK_PLAN_CHANGED_EVENT,
    MILK_SCHEDULE_RESCHEDULE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    MilkPlanCreateActionHandler,
    MilkScheduleRescheduleActionHandler,
    PlanDeleteActionHandler,
    PlanTaskCompleteActionHandler,
    PlanTaskCreateActionHandler,
    PlanTaskDeleteActionHandler,
    PlanTaskUpdateActionHandler,
    PregnancyPlanCreateActionHandler,
)
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.workers.errors import PermanentJobError
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.pregnancy_plan_flow import (
    build_pregnancy_plan_result,
)


PRIVATE_PREGNANCY_PLAN_CONTENT = "private thyroid medication and birth plan card"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"


def test_milk_plan_create_action_handler_creates_plan_through_service() -> None:
    service = FakePlansService()
    action = _action(
        apply_payload={
            "title": "Increase pumping consistency",
            "summary": "Pump after morning and evening feeds.",
            "payload": {
                "start_date": "2026-07-13",
                "days": 2,
                "tasks": [{"title": "Morning pump", "time": "08:00", "task_type": "pumping"}],
            },
        }
    )

    result = asyncio.run(MilkPlanCreateActionHandler(service=service)(action))

    assert result.resource_type == "plan"
    assert result.resource_id == str(service.plan.id)
    assert result.details == {
        "plan_type": "milk_management",
        "task_count": 2,
        "agent_action_id": str(action.id),
        "agent_run_id": str(action.run_id),
    }
    assert service.create_plan_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_plan_kwargs["plan_type"] == "milk_management"
    assert service.create_plan_kwargs["title"] == "Increase pumping consistency"
    assert service.create_plan_kwargs["summary"] == "Pump after morning and evening feeds."
    assert service.create_plan_kwargs["source"] == "agent_action"
    assert service.create_plan_kwargs["payload"]["start_date"] == "2026-07-13"
    assert service.create_plan_kwargs["payload"]["agent_action_id"] == str(action.id)
    assert service.create_plan_kwargs["idempotency_key"] == "idem-action"
    assert len(service.create_task_kwargs_list) == 2
    assert [call["task_date"].isoformat() for call in service.create_task_kwargs_list] == [
        "2026-07-13",
        "2026-07-14",
    ]
    assert all(call["plan_id"] == service.plan.id for call in service.create_task_kwargs_list)
    assert all(call["payload"]["source"] == "agent_action" for call in service.create_task_kwargs_list)
    assert len(result.application_events) == 1
    changed_event = result.application_events[0]
    assert changed_event.event_type == MILK_PLAN_CHANGED_EVENT
    assert changed_event.payload == {
        "operation": "created",
        "reason": "created",
        "plan_id": str(service.plan.id),
        "plan_type": "milk_management",
        "source": "agent_action",
        "affected_dates": ["2026-07-13", "2026-07-14"],
    }
    rendered_event = json.dumps(changed_event.payload, ensure_ascii=False)
    assert "Pump after morning and evening feeds" not in rendered_event
    assert "Morning pump" not in rendered_event


def test_milk_plan_changed_event_contains_only_bounded_dates_and_no_private_plan_content() -> None:
    service = FakePlansService()
    private_summary = "private lactation health history and supply target"
    action = _action(
        apply_payload={
            "title": "Private milk plan title",
            "summary": private_summary,
            "payload": {
                "start_date": "2026-07-04",
                "days": 30,
                "tasks": [
                    {
                        "title": "private task",
                        "time": "08:00",
                        "task_type": "pumping",
                        "health_note": "private diagnosis",
                    }
                ],
            },
        }
    )

    result = asyncio.run(MilkPlanCreateActionHandler(service=service)(action))

    changed_event = result.application_events[0]
    assert len(changed_event.payload["affected_dates"]) == 30
    assert changed_event.payload["affected_dates"][0] == "2026-07-04"
    assert changed_event.payload["affected_dates"][-1] == "2026-08-02"
    rendered_event = json.dumps(changed_event.payload, ensure_ascii=False)
    assert private_summary not in rendered_event
    assert "private task" not in rendered_event
    assert "private diagnosis" not in rendered_event


def test_milk_plan_create_action_handler_rejects_missing_title() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(MilkPlanCreateActionHandler(service=FakePlansService())(_action(apply_payload={})))

    assert exc_info.value.code == "missing_plan_title"


def test_milk_plan_create_action_handler_rejects_a_plan_that_cannot_reach_schedule() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            MilkPlanCreateActionHandler(service=FakePlansService())(
                _action(apply_payload={"title": "Plan without tasks", "payload": {"days": 7}})
            )
        )

    assert exc_info.value.code == "invalid_milk_plan_schedule"


def test_milk_plan_create_action_handler_rejects_an_expired_analysis_before_side_effects() -> None:
    service = FakePlansService()
    action = _action(
        apply_payload={
            "title": "Expired milk plan",
            "payload": {
                "days": 1,
                "tasks": [{"title": "Morning pump", "time": "08:00", "task_type": "pumping"}],
            },
        },
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(MilkPlanCreateActionHandler(service=service)(action))

    assert exc_info.value.code == "milk_analysis_expired_before_plan"
    assert service.create_plan_kwargs == {}


def test_milk_schedule_reschedule_action_emits_authoritative_change_event() -> None:
    service = FakePlansService()
    service.task.plan_id = service.plan.id
    service.task.task_date = date(2026, 7, 14)
    service.task.task_time = "08:00"
    action = _action(
        action_type=MILK_SCHEDULE_RESCHEDULE_ACTION,
        target_type="plan",
        apply_payload={
            "plan_id": str(service.plan.id),
            "updates": [
                {
                    "task_id": str(service.task.id),
                    "expected_plan_id": str(service.plan.id),
                    "expected_task_date": "2026-07-14",
                    "expected_task_time": "08:00",
                    "new_task_date": "2026-07-14",
                    "new_task_time": "07:30",
                }
            ],
        },
    )

    result = asyncio.run(MilkScheduleRescheduleActionHandler(service=service)(action))

    assert result.resource_id == str(service.plan.id)
    assert service.reschedule_milk_tasks_kwargs["owner_user_id"] == action.actor_user_id
    assert result.application_events[0].event_type == MILK_PLAN_CHANGED_EVENT
    assert result.application_events[0].payload == {
        "operation": "rescheduled",
        "reason": "schedule_adjustment",
        "plan_id": str(service.plan.id),
        "plan_type": "milk_management",
        "source": "agent_action",
        "affected_dates": ["2026-07-14"],
        "task_ids": [str(service.task.id)],
    }


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


def test_pregnancy_plan_create_action_handler_persists_personalized_and_checkup_card() -> None:
    service = FakePlansService()
    result_payload = build_pregnancy_plan_result(
        {
            "current_week": "24周",
            "ivf": "否",
            "fetus_count": "单胎",
            "age": 30,
            "first_birth": "是",
            "birth_path": "顺产",
            "personalized_followup_records": [
                {
                    "topic": "doctor_special_notes_followup",
                    "answer": "医生让我下周复查",
                    "plan_impact": "来自客户端的不可信覆盖",
                }
            ],
            "checkup_status": "已上传产检记录",
            "owner_user_id": "other-owner-id",
        }
    )
    action = _action(
        action_type=PREGNANCY_PLAN_CREATE_ACTION,
        target_type="plan",
        apply_payload={
            "title": "孕期计划",
            "payload": {
                "plan_context": {"current_week": "24周"},
                "card": result_payload["card"],
            },
        },
    )

    asyncio.run(PregnancyPlanCreateActionHandler(service=service)(action))

    persisted_card = service.create_plan_kwargs["payload"]["card"]["card_json"]
    current_items = persisted_card["todo_plan"]["periods"][0]["items"]
    assert any(item["id"].startswith("personalized_followup_") for item in current_items)
    assert any(item["id"] == "review_uploaded_checkup_records" for item in current_items)
    assert "来自客户端的不可信覆盖" not in str(persisted_card)
    assert "other-owner-id" not in str(persisted_card)


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
    service.task.id = task_id
    service.task.owner_user_id = action.actor_user_id

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
    service.plan.id = plan_id
    service.plan.owner_user_id = action.actor_user_id

    result = asyncio.run(PlanDeleteActionHandler(service=service)(action))

    assert result.resource_type == "plan"
    assert result.resource_id == str(plan_id)
    assert service.delete_plan_kwargs["owner_user_id"] == action.actor_user_id
    assert service.delete_plan_kwargs["plan_id"] == plan_id


def test_milk_task_update_delete_and_plan_delete_emit_change_operations() -> None:
    service = FakePlansService()
    owner = uuid4()
    service.plan.owner_user_id = owner
    service.plan.payload = {"start_date": "2026-07-14", "days": 2}
    service.task.owner_user_id = owner
    service.task.plan_id = service.plan.id
    service.task.task_date = date(2026, 7, 14)
    service.task.task_time = "08:00"

    update_action = _action(
        action_type=PLAN_TASK_UPDATE_ACTION,
        target_type="plan_task",
        apply_payload={"task_id": str(service.task.id), "task_time": "08:30"},
    )
    update_action.actor_user_id = owner
    updated = asyncio.run(PlanTaskUpdateActionHandler(service=service)(update_action))

    delete_action = _action(
        action_type=PLAN_TASK_DELETE_ACTION,
        target_type="plan_task",
        apply_payload={"task_id": str(service.task.id)},
    )
    delete_action.actor_user_id = owner
    deleted = asyncio.run(PlanTaskDeleteActionHandler(service=service)(delete_action))

    plan_delete_action = _action(
        action_type=PLAN_DELETE_ACTION,
        target_type="plan",
        apply_payload={"plan_id": str(service.plan.id)},
    )
    plan_delete_action.actor_user_id = owner
    plan_deleted = asyncio.run(PlanDeleteActionHandler(service=service)(plan_delete_action))

    assert updated.application_events[0].payload["operation"] == "updated"
    assert deleted.application_events[0].payload["operation"] == "deleted"
    assert plan_deleted.application_events[0].payload == {
        "operation": "deleted",
        "reason": "plan_deleted",
        "plan_id": str(service.plan.id),
        "plan_type": "milk_management",
        "source": "agent_action",
        "affected_dates": ["2026-07-14", "2026-07-15"],
    }


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
        self.create_task_kwargs_list = []
        self.set_task_completed_kwargs = {}
        self.update_task_kwargs = {}
        self.delete_task_kwargs = {}
        self.delete_plan_kwargs = {}
        self.reschedule_milk_tasks_kwargs = {}

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
        self.create_task_kwargs_list.append(kwargs)
        self.task.owner_user_id = kwargs["owner_user_id"]
        self.task.plan_id = kwargs["plan_id"]
        self.task.task_date = kwargs["task_date"]
        self.task.task_time = kwargs["task_time"]
        self.task.title = kwargs["title"]
        self.task.description = kwargs["description"]
        self.task.payload = kwargs["payload"]
        return self.task

    async def get_plan(self, **kwargs):
        if kwargs["owner_user_id"] != self.plan.owner_user_id or kwargs["plan_id"] != self.plan.id:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return self.plan

    async def get_task(self, **kwargs):
        if kwargs["owner_user_id"] != self.task.owner_user_id or kwargs["task_id"] != self.task.id:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
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

    async def reschedule_milk_tasks(self, **kwargs):
        self.reschedule_milk_tasks_kwargs = kwargs
        update = kwargs["updates"][0]
        self.task.task_date = date.fromisoformat(update["new_task_date"])
        self.task.task_time = update["new_task_time"]
        self.task.owner_user_id = kwargs["owner_user_id"]
        return [self.task]


def _action(
    *,
    apply_payload: dict,
    action_type: str = MILK_PLAN_CREATE_ACTION,
    target_type: str = "plan",
    expires_at: datetime | None = None,
) -> AgentAction:
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
        expires_at=expires_at,
        error_code="",
    )
