import asyncio
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.validation import validate_tool_output
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
from app.agents.cozymate.tools.handlers.schedule import ScheduleTimelineMutateToolHandler
from app.modules.auth import CurrentUser


@pytest.mark.parametrize(
    ("operation", "record_type", "extra_args", "expected_action"),
    [
        ("create", "feeding", {"feed_type": "bottle", "volume_ml": 90}, FEEDING_RECORD_CREATE_ACTION),
        ("update", "feeding", {"volume_ml": 75}, FEEDING_RECORD_UPDATE_ACTION),
        ("delete", "feeding", {}, FEEDING_RECORD_DELETE_ACTION),
        ("create", "pumping", {"milk_volume_ml": 95}, PUMPING_RECORD_CREATE_ACTION),
        ("update", "pumping", {"milk_volume_ml": 105}, PUMPING_RECORD_UPDATE_ACTION),
        ("delete", "pumping", {}, PUMPING_RECORD_DELETE_ACTION),
        ("create", "growth", {"weight_kg": 5.4}, GROWTH_RECORD_CREATE_ACTION),
        ("update", "growth", {"weight_kg": 5.5}, GROWTH_RECORD_UPDATE_ACTION),
        ("delete", "growth", {}, GROWTH_RECORD_DELETE_ACTION),
    ],
)
def test_schedule_timeline_mutate_routes_every_execution_crud_variant(
    operation: str,
    record_type: str,
    extra_args: dict,
    expected_action: str,
) -> None:
    runtime = FakeRuntimeService()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=object(),
    )
    record_id = uuid4()
    args = {
        "operation": operation,
        "entry_type": "execution",
        "record_type": record_type,
        **extra_args,
    }
    if operation == "create":
        args["occurred_at"] = "2026-07-24T09:10:00+08:00"
    else:
        args["record_id"] = str(record_id)

    result = asyncio.run(handler.execute(_context(args=args)))

    assert runtime.calls[-1]["action_type"] == expected_action
    if operation != "create":
        assert runtime.calls[-1]["target_id"] == str(record_id)
    assert result["operation"] == operation
    assert result["entry_type"] == "execution"
    assert result["domain"] == "lactation"
    assert result["record_type"] == record_type
    assert result["status"] == "lactation_record_change_applied"
    validate_tool_output(
        schema=default_tool_registry().get("schedule_timeline_mutate").output_schema,
        value=result,
    )


def test_schedule_timeline_mutate_maps_common_occurrence_time_to_domain_field() -> None:
    runtime = FakeRuntimeService()
    plan_task_id = uuid4()
    handler = ScheduleTimelineMutateToolHandler(
        runtime_service=runtime,
        plans_service=object(),
    )

    asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "create",
                    "entry_type": "execution",
                    "record_type": "feeding",
                    "plan_task_id": str(plan_task_id),
                    "occurred_at": "2026-07-24T09:10:00+08:00",
                    "feed_type": "bottle",
                    "volume_ml": 90,
                }
            )
        )
    )

    assert runtime.calls[-1]["apply_payload"]["plan_task_id"] == str(plan_task_id)
    assert runtime.calls[-1]["apply_payload"]["feed_time"] == "2026-07-24T09:10:00+08:00"


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


def _context(*, args: dict) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=CurrentUser(
            user_id=uuid4(),
            subject="record-user",
            session_id="record-session",
            token_id="record-token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name="schedule_timeline_mutate",
        call_id="timeline-execution-call",
        args=args,
        thread_id=uuid4(),
    )
