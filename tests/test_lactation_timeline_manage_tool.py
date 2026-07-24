import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.cozymate.actions.plans import (
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
)
from app.agents.cozymate.actions.records import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    FEEDING_RECORD_UPDATE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_DELETE_ACTION,
    PUMPING_RECORD_UPDATE_ACTION,
)
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.milk import LactationTimelineManageToolHandler
from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.validation import validate_tool_output
from app.core.errors import ApiError
from app.modules.auth import CurrentUser


def test_lactation_timeline_manage_contract_is_one_described_write_entry() -> None:
    contract = default_tool_registry().get("lactation_timeline_write")

    assert contract.domain == "lactation_timeline"
    assert contract.effect_scope == "user_resource"
    assert "action_type" not in type(contract).model_fields
    assert "新增、更新、删除" in contract.description
    assert "实际记录" in contract.description
    assert "计划日程" in contract.description

    schema = contract.input_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation", "item_type"]
    assert schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
        "set_status",
        "reschedule",
    ]
    assert schema["properties"]["item_type"]["enum"] == [
        "schedule",
        "feeding",
        "pumping",
        "growth",
    ]
    for field_name, field_schema in schema["properties"].items():
        assert field_schema.get("description"), f"{field_name} lacks a description"

    output_schema = contract.output_schema
    assert output_schema is not None
    assert output_schema["additionalProperties"] is False
    for field_name, field_schema in output_schema["properties"].items():
        assert field_schema.get("description"), f"output.{field_name} lacks a description"


def test_lactation_timeline_manage_creates_linked_feeding_record() -> None:
    actor = _user()
    runtime = FakeRuntimeService()
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(),
    )
    plan_task_id = uuid4()

    result = asyncio.run(
        handler.execute(
            _context(
                actor=actor,
                args={
                    "operation": "create",
                    "item_type": "feeding",
                    "plan_task_id": str(plan_task_id),
                    "occurred_at": "2026-07-24T09:10:00+08:00",
                    "feed_type": "bottle",
                    "volume_ml": 90,
                },
            )
        )
    )

    call = runtime.calls[-1]
    assert call["action_type"] == FEEDING_RECORD_CREATE_ACTION
    assert call["target_type"] == "feeding_record"
    assert call["apply_payload"]["plan_task_id"] == str(plan_task_id)
    assert call["apply_payload"]["feed_time"] == "2026-07-24T09:10:00+08:00"
    assert result["operation"] == "create"
    assert result["item_type"] == "feeding"
    assert result["status"] == "timeline_change_applied"
    assert result["write_succeeded"] is True
    validate_tool_output(
        schema=default_tool_registry().get("lactation_timeline_write").output_schema,
        value=result,
    )


def test_lactation_timeline_manage_updates_pumping_record() -> None:
    runtime = FakeRuntimeService()
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(),
    )
    record_id = uuid4()

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "update",
                    "item_type": "pumping",
                    "item_id": str(record_id),
                    "milk_volume_ml": 105,
                    "duration_seconds": 900,
                }
            )
        )
    )

    call = runtime.calls[-1]
    assert call["action_type"] == PUMPING_RECORD_UPDATE_ACTION
    assert call["target_id"] == str(record_id)
    assert call["apply_payload"] == {
        "record_id": str(record_id),
        "milk_volume_ml": 105.0,
        "duration_seconds": 900,
    }
    assert result["status"] == "timeline_change_applied"


@pytest.mark.parametrize(
    ("operation", "item_type", "extra_args", "expected_action"),
    [
        ("update", "feeding", {"volume_ml": 75}, FEEDING_RECORD_UPDATE_ACTION),
        ("delete", "feeding", {}, FEEDING_RECORD_DELETE_ACTION),
        ("create", "pumping", {"milk_volume_ml": 95}, PUMPING_RECORD_CREATE_ACTION),
        ("delete", "pumping", {}, PUMPING_RECORD_DELETE_ACTION),
        ("create", "growth", {"weight_kg": 5.4}, GROWTH_RECORD_CREATE_ACTION),
        ("update", "growth", {"weight_kg": 5.5}, GROWTH_RECORD_UPDATE_ACTION),
        ("delete", "growth", {}, GROWTH_RECORD_DELETE_ACTION),
    ],
)
def test_lactation_timeline_manage_routes_every_record_crud_variant(
    operation: str,
    item_type: str,
    extra_args: dict,
    expected_action: str,
) -> None:
    runtime = FakeRuntimeService()
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(),
    )
    item_id = uuid4()
    args = {
        "operation": operation,
        "item_type": item_type,
        **extra_args,
    }
    if operation == "create":
        args["occurred_at"] = "2026-07-24T09:10:00+08:00"
    else:
        args["item_id"] = str(item_id)

    result = asyncio.run(handler.execute(_context(args=args)))

    assert runtime.calls[-1]["action_type"] == expected_action
    if operation != "create":
        assert runtime.calls[-1]["target_id"] == str(item_id)
    assert result["status"] == "timeline_change_applied"


def test_lactation_timeline_manage_creates_typed_schedule_only_in_milk_plan() -> None:
    runtime = FakeRuntimeService()
    plan_id = uuid4()
    plans = FakePlansService(plan_id=plan_id)
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=plans,
    )

    asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "create",
                    "item_type": "schedule",
                    "plan_id": str(plan_id),
                    "event_type": "pumping",
                    "task_date": "2026-07-25",
                    "task_time": "08:30",
                    "title": "晨间吸奶",
                }
            )
        )
    )

    call = runtime.calls[-1]
    assert plans.loaded_plan_id == plan_id
    assert call["action_type"] == PLAN_TASK_CREATE_ACTION
    assert call["apply_payload"]["plan_id"] == str(plan_id)
    assert call["apply_payload"]["payload"] == {"task_type": "pumping"}


def test_lactation_timeline_manage_sets_schedule_status_after_owner_scoped_lookup() -> None:
    runtime = FakeRuntimeService()
    task_id = uuid4()
    plan_id = uuid4()
    plans = FakePlansService(plan_id=plan_id, task_id=task_id)
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=plans,
    )

    asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "set_status",
                    "item_type": "schedule",
                    "item_id": str(task_id),
                    "completed": True,
                }
            )
        )
    )

    call = runtime.calls[-1]
    assert plans.loaded_task_id == task_id
    assert call["action_type"] == PLAN_TASK_COMPLETE_ACTION
    assert call["apply_payload"] == {
        "task_id": str(task_id),
        "completed": True,
    }


@pytest.mark.parametrize(
    ("operation", "extra_args", "expected_action"),
    [
        ("update", {"task_time": "09:15"}, PLAN_TASK_UPDATE_ACTION),
        ("delete", {"reason": "不再需要"}, PLAN_TASK_DELETE_ACTION),
    ],
)
def test_lactation_timeline_manage_routes_schedule_update_and_delete(
    operation: str,
    extra_args: dict,
    expected_action: str,
) -> None:
    runtime = FakeRuntimeService()
    task_id = uuid4()
    plan_id = uuid4()
    plans = FakePlansService(plan_id=plan_id, task_id=task_id)
    handler = LactationTimelineManageToolHandler(
        runtime_service=runtime,
        plans_service=plans,
    )

    result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": operation,
                    "item_type": "schedule",
                    "item_id": str(task_id),
                    **extra_args,
                }
            )
        )
    )

    assert plans.loaded_task_id == task_id
    assert runtime.calls[-1]["action_type"] == expected_action
    assert result["status"] == "timeline_change_applied"


def test_lactation_timeline_manage_rejects_non_milk_schedule_and_invalid_operation_pair() -> None:
    plan_id = uuid4()
    non_milk_handler = LactationTimelineManageToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(plan_id=plan_id, plan_type="pregnancy"),
    )
    with pytest.raises(ApiError) as non_milk_error:
        asyncio.run(
            non_milk_handler.execute(
                _context(
                    args={
                        "operation": "create",
                        "item_type": "schedule",
                        "plan_id": str(plan_id),
                        "task_date": "2026-07-25",
                        "task_time": "08:30",
                        "title": "不应写入",
                    }
                )
            )
        )
    assert non_milk_error.value.code == "validation_failed"

    handler = LactationTimelineManageToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(),
    )
    with pytest.raises(ApiError) as invalid_pair_error:
        asyncio.run(
            handler.execute(
                _context(
                    args={
                        "operation": "set_status",
                        "item_type": "feeding",
                        "item_id": str(uuid4()),
                        "completed": True,
                    }
                )
            )
        )
    assert invalid_pair_error.value.code == "validation_failed"


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
        plan_type: str = "milk_management",
    ) -> None:
        self.plan_id = plan_id or uuid4()
        self.task_id = task_id or uuid4()
        self.plan_type = plan_type
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
            payload={"task_type": "pumping"},
        )


def _context(*, actor=None, args=None) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=actor or _user(),
        run_id=uuid4(),
        tool_name="lactation_timeline_write",
        call_id="timeline-manage-call",
        args=args or {},
        thread_id=uuid4(),
    )


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=uuid4(),
        subject="timeline-user",
        session_id="timeline-session",
        token_id="timeline-token",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
