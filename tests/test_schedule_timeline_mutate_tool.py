import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.validation import validate_tool_output
from app.agents.cozymate.actions.plans import (
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
)
from app.agents.cozymate.actions.records import (
    FEEDING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
)
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.schedule import ScheduleTimelineMutateToolHandler
from app.core.errors import ApiError
from app.modules.auth import CurrentUser


def test_schedule_timeline_mutate_contract_is_generic_and_described() -> None:
    contract = default_tool_registry().get("schedule_timeline_mutate")

    assert contract.domain == "schedule_timeline"
    assert contract.effect_scope == "user_resource"
    assert "跨领域" in contract.description
    assert "实际记录" in contract.description
    schema = contract.input_schema
    assert schema["required"] == ["operation", "entry_type"]
    assert schema["properties"]["entry_type"]["enum"] == ["schedule", "execution"]
    assert schema["properties"]["domain"]["enum"] == [
        "lactation",
        "pregnancy",
        "postpartum_recovery",
        "general",
    ]
    assert schema["properties"]["record_type"]["enum"] == [
        "feeding",
        "pumping",
        "growth",
    ]
    for field_name, field_schema in schema["properties"].items():
        assert field_schema.get("description"), f"{field_name} lacks a description"

    output_schema = contract.output_schema
    assert output_schema is not None
    for field_name, field_schema in output_schema["properties"].items():
        assert field_schema.get("description"), f"output.{field_name} lacks a description"


def test_schedule_timeline_mutate_creates_cross_domain_schedule_with_persisted_domain() -> None:
    runtime = FakeRuntimeService()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(plan_type="pregnancy"),
    )

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "create",
                    "entry_type": "schedule",
                    "domain": "pregnancy",
                    "event_type": "appointment",
                    "task_date": "2026-07-25",
                    "title": "产检",
                }
            )
        )
    )

    call = runtime.calls[-1]
    assert call["action_type"] == PLAN_TASK_CREATE_ACTION
    assert call["apply_payload"]["payload"] == {
        "domain": "pregnancy",
        "event_type": "appointment",
    }
    assert result["operation"] == "create"
    assert result["domain"] == "pregnancy"
    assert result["status"] == "schedule_change_applied"
    validate_tool_output(
        schema=default_tool_registry().get("schedule_timeline_mutate").output_schema,
        value=result,
    )


def test_schedule_timeline_mutate_persists_lactation_task_type_for_compatibility() -> None:
    runtime = FakeRuntimeService()
    plan_id = uuid4()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(plan_id=plan_id, plan_type="milk_management"),
    )

    asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "create",
                    "entry_type": "schedule",
                    "domain": "lactation",
                    "plan_id": str(plan_id),
                    "event_type": "pumping",
                    "task_date": "2026-07-25",
                    "task_time": "08:30",
                    "title": "晨间吸奶",
                }
            )
        )
    )

    assert runtime.calls[-1]["apply_payload"]["payload"] == {
        "domain": "lactation",
        "event_type": "pumping",
        "task_type": "pumping",
    }


@pytest.mark.parametrize(
    ("operation", "extra_args", "expected_action"),
    [
        ("update", {"task_time": "09:15"}, PLAN_TASK_UPDATE_ACTION),
        ("delete", {"reason": "不再需要"}, PLAN_TASK_DELETE_ACTION),
        ("set_status", {"completed": True}, PLAN_TASK_COMPLETE_ACTION),
        ("reschedule", {"task_date": "2026-07-26", "task_time": "11:00"}, PLAN_TASK_UPDATE_ACTION),
    ],
)
def test_schedule_timeline_mutate_routes_existing_schedule_operations(
    operation: str,
    extra_args: dict,
    expected_action: str,
) -> None:
    runtime = FakeRuntimeService()
    task_id = uuid4()
    plans = FakePlansService(task_id=task_id, plan_type="postpartum_recovery")
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=plans,
    )

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": operation,
                    "entry_type": "schedule",
                    "task_id": str(task_id),
                    **extra_args,
                }
            )
        )
    )

    assert plans.loaded_task_id == task_id
    assert runtime.calls[-1]["action_type"] == expected_action
    assert result["operation"] == operation
    assert result["domain"] == "postpartum_recovery"


def test_schedule_timeline_mutate_rejects_cross_domain_plan_assignment() -> None:
    plan_id = uuid4()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(plan_id=plan_id, plan_type="pregnancy"),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    args={
                        "operation": "create",
                        "entry_type": "schedule",
                        "domain": "lactation",
                        "plan_id": str(plan_id),
                        "event_type": "pumping",
                        "task_date": "2026-07-25",
                        "task_time": "08:30",
                        "title": "不应写入",
                    }
                )
            )
        )

    assert exc_info.value.code == "validation_failed"


@pytest.mark.parametrize(
    ("event_type", "measurement_field", "measurement", "expected_action"),
    [
        ("feeding", "volume_ml", 90, FEEDING_RECORD_CREATE_ACTION),
        ("pumping", "milk_volume_ml", 105, PUMPING_RECORD_CREATE_ACTION),
    ],
)
def test_completing_lactation_task_creates_measured_execution_and_completes_via_record_action(
    event_type: str,
    measurement_field: str,
    measurement: int,
    expected_action: str,
) -> None:
    runtime = FakeRuntimeService()
    task_id = uuid4()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(
            task_id=task_id,
            plan_type="milk_management",
            task_domain="lactation",
            task_event_type=event_type,
        ),
    )

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "set_status",
                    "entry_type": "schedule",
                    "task_id": str(task_id),
                    "completed": True,
                    "occurred_at": "2026-07-24T09:10:00+08:00",
                    measurement_field: measurement,
                    **({"feed_type": "bottle"} if event_type == "feeding" else {}),
                }
            )
        )
    )

    assert [call["action_type"] for call in runtime.calls] == [expected_action]
    assert runtime.calls[0]["apply_payload"]["plan_task_id"] == str(task_id)
    assert result["operation"] == "set_status"
    assert result["entry_type"] == "schedule"
    assert result["record_type"] == event_type


@pytest.mark.parametrize("event_type", ["feeding", "pumping"])
def test_completing_lactation_task_rejects_missing_milk_measurement(event_type: str) -> None:
    runtime = FakeRuntimeService()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(
            plan_type="milk_management",
            task_domain="lactation",
            task_event_type=event_type,
        ),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    args={
                        "operation": "set_status",
                        "entry_type": "schedule",
                        "task_id": str(uuid4()),
                        "completed": True,
                        "occurred_at": "2026-07-24T09:10:00+08:00",
                    }
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert runtime.calls == []


def test_restoring_lactation_task_requires_deleting_its_execution_record() -> None:
    runtime = FakeRuntimeService()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(
            plan_type="milk_management",
            task_domain="lactation",
            task_event_type="pumping",
        ),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    args={
                        "operation": "set_status",
                        "entry_type": "schedule",
                        "task_id": str(uuid4()),
                        "completed": False,
                    }
                )
            )
        )

    assert exc_info.value.code == "unsupported_operation"
    assert "execution record" in exc_info.value.message
    assert runtime.calls == []


def test_schedule_timeline_mutate_creates_unplanned_lactation_execution() -> None:
    runtime = FakeRuntimeService()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(),
    )

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "create",
                    "entry_type": "execution",
                    "record_type": "pumping",
                    "occurred_at": "2026-07-24T09:10:00+08:00",
                    "milk_volume_ml": 95,
                }
            )
        )
    )

    assert runtime.calls[-1]["action_type"] == PUMPING_RECORD_CREATE_ACTION
    assert "plan_task_id" not in runtime.calls[-1]["apply_payload"]
    assert result["entry_type"] == "execution"
    assert result["record_type"] == "pumping"
    validate_tool_output(
        schema=default_tool_registry().get("schedule_timeline_mutate").output_schema,
        value=result,
    )


class FakeRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        return AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["owner_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs.get("target_id", ""),
            status="applied",
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            error_code="",
        )


class FakePlansService:
    def __init__(
        self,
        *,
        plan_id=None,
        task_id=None,
        plan_type: str = "general",
        task_domain: str = "postpartum_recovery",
        task_event_type: str = "exercise",
    ) -> None:
        self.plan_id = plan_id or uuid4()
        self.task_id = task_id or uuid4()
        self.plan_type = plan_type
        self.task_domain = task_domain
        self.task_event_type = task_event_type
        self.loaded_plan_id = None
        self.loaded_task_id = None

    async def get_plan(self, *, owner_user_id, plan_id):
        self.loaded_plan_id = plan_id
        return SimpleNamespace(id=plan_id, plan_type=self.plan_type)

    async def get_task(self, *, owner_user_id, task_id):
        self.loaded_task_id = task_id
        return SimpleNamespace(
            id=task_id,
            plan_id=self.plan_id,
            payload={
                "domain": self.task_domain,
                "event_type": self.task_event_type,
            },
        )


def _context(*, args: dict) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=CurrentUser(
            user_id=uuid4(),
            subject="timeline-user",
            session_id="timeline-session",
            token_id="timeline-token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name="schedule_timeline_mutate",
        call_id="timeline-write-call",
        args=args,
        thread_id=uuid4(),
    )
