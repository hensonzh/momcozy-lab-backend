import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.validation import validate_tool_output
from app.agents.cozymate.actions.plans import (
    MILK_PLAN_CREATE_ACTION,
    PLAN_DELETE_ACTION,
    PLAN_UPDATE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
)
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.plans import PlanMutateToolHandler, PlanReadToolHandler
from app.core.errors import ApiError
from app.modules.auth import CurrentUser


def test_plan_tool_contracts_unify_persisted_plan_operations() -> None:
    registry = default_tool_registry()

    assert {"plan_read", "plan_mutate"} <= set(registry.names_for_sdk())

    read_contract = registry.get("plan_read")
    mutate_contract = registry.get("plan_mutate")
    assert read_contract.effect_scope == "none"
    assert mutate_contract.effect_scope == "user_resource"
    assert mutate_contract.action_types == (
        MILK_PLAN_CREATE_ACTION,
        PREGNANCY_PLAN_CREATE_ACTION,
        PLAN_UPDATE_ACTION,
        PLAN_DELETE_ACTION,
    )
    assert mutate_contract.input_schema["required"] == ["operation"]
    assert mutate_contract.input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
    ]
    plan_type_schema = mutate_contract.input_schema["properties"]["plan_type"]
    assert "enum" not in plan_type_schema
    assert plan_type_schema["examples"] == ["milk_management", "pregnancy"]
    for schema in (read_contract.input_schema, mutate_contract.input_schema):
        for field_name, field_schema in schema["properties"].items():
            assert field_schema.get("description"), f"{field_name} lacks a description"


def test_plan_read_lists_owner_scoped_plans_with_safe_type_specific_content() -> None:
    owner_user_id = uuid4()
    plans = [
        _plan(
            owner_user_id=owner_user_id,
            plan_type="milk_management",
            payload={
                "direction": "maintain",
                "start_date": "2026-07-27",
                "days": 7,
                "strategy_summary": "保持当前节奏",
                "analysis_context_fingerprint": "must-not-leak",
                "analysis_workflow_state_id": "must-not-leak",
                "tasks": [{"title": "由时间线读取"}],
            },
        ),
        _plan(
            owner_user_id=owner_user_id,
            plan_type="pregnancy",
            payload={
                "card": {"card_type": "birth_journey_plan_card", "card_json": {"title": "孕期计划"}},
                "plan_context": {"medical_notes": "must-not-leak"},
                "lineage": {"workflow_state_id": "must-not-leak"},
            },
        ),
    ]
    service = FakePlansService(plans=plans)
    handler = PlanReadToolHandler(plans_service=service)

    result = asyncio.run(
        handler.execute(
            _context(
                owner_user_id=owner_user_id,
                tool_name="plan_read",
                args={"mode": "list", "include_content": True},
            )
        )
    )

    assert service.list_kwargs == {
        "owner_user_id": owner_user_id,
        "plan_type": "",
        "status": "active",
        "limit": 20,
    }
    assert result["mode"] == "list"
    assert result["count"] == 2
    assert result["plans"][0]["content"] == {
        "direction": "maintain",
        "start_date": "2026-07-27",
        "days": 7,
        "strategy_summary": "保持当前节奏",
    }
    assert result["plans"][1]["content"] == {
        "card": {"card_type": "birth_journey_plan_card", "card_json": {"title": "孕期计划"}},
    }
    assert "must-not-leak" not in str(result)
    validate_tool_output(
        schema=default_tool_registry().get("plan_read").output_schema,
        value=result,
    )


def test_plan_read_detail_requires_plan_id_and_returns_version_for_updates() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="pregnancy")
    handler = PlanReadToolHandler(plans_service=FakePlansService(plans=[plan]))

    result = asyncio.run(
        handler.execute(
            _context(
                owner_user_id=owner_user_id,
                tool_name="plan_read",
                args={"mode": "detail", "plan_id": str(plan.id)},
            )
        )
    )

    assert result["plan"]["plan_id"] == str(plan.id)
    assert result["plan"]["version"] == 3

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    owner_user_id=owner_user_id,
                    tool_name="plan_read",
                    args={"mode": "detail"},
                )
            )
        )
    assert exc_info.value.code == "validation_failed"


@pytest.mark.parametrize(
    ("plan_type", "expected_action"),
    [
        ("milk_management", MILK_PLAN_CREATE_ACTION),
        ("pregnancy", PREGNANCY_PLAN_CREATE_ACTION),
    ],
)
def test_plan_mutate_routes_create_by_plan_type(
    plan_type: str,
    expected_action: str,
) -> None:
    milk = CapturingPlanOperationHandler(action_type=MILK_PLAN_CREATE_ACTION)
    pregnancy = CapturingPlanOperationHandler(action_type=PREGNANCY_PLAN_CREATE_ACTION)
    handler = PlanMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(),
        milk_create_handler=milk,
        pregnancy_create_handler=pregnancy,
    )

    result = asyncio.run(
        handler.execute(
            _context(
                tool_name="plan_mutate",
                args={"operation": "create", "plan_type": plan_type},
            )
        )
    )

    selected = milk if plan_type == "milk_management" else pregnancy
    assert selected.calls
    assert result["operation"] == "create"
    assert result["plan_type"] == plan_type
    assert result["action_type"] == expected_action


def test_plan_mutate_create_requires_a_supported_plan_type() -> None:
    handler = PlanMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(),
    )

    with pytest.raises(ApiError) as missing:
        asyncio.run(
            handler.execute(
                _context(
                    tool_name="plan_mutate",
                    args={"operation": "create"},
                )
            )
        )
    assert missing.value.code == "validation_failed"

    with pytest.raises(ApiError) as unsupported:
        asyncio.run(
            handler.execute(
                _context(
                    tool_name="plan_mutate",
                    args={"operation": "create", "plan_type": "general"},
                )
            )
        )
    assert unsupported.value.code == "unsupported_plan_type"


def test_plan_mutate_delete_derives_type_from_owner_scoped_plan() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="milk_management")
    delete = CapturingPlanOperationHandler(action_type=PLAN_DELETE_ACTION)
    handler = PlanMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(plans=[plan]),
        delete_handler=delete,
    )

    result = asyncio.run(
        handler.execute(
            _context(
                owner_user_id=owner_user_id,
                tool_name="plan_mutate",
                args={"operation": "delete", "plan_id": str(plan.id)},
            )
        )
    )

    assert delete.calls
    assert result["plan_type"] == "milk_management"
    assert result["plan_id"] == str(plan.id)
    assert result["action_type"] == PLAN_DELETE_ACTION


def test_plan_mutate_delete_uses_runtime_owned_stable_idempotency() -> None:
    owner_user_id = uuid4()
    plan = _plan(
        owner_user_id=owner_user_id,
        plan_type="milk_management",
    )
    runtime = FakeRuntimeService()
    handler = PlanMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(plans=[plan]),
    )
    context = _context(
        owner_user_id=owner_user_id,
        tool_name="plan_mutate",
        args={
            "operation": "delete",
            "plan_id": str(plan.id),
            "idempotency_key": "model-controlled-key",
        },
    )

    asyncio.run(handler.execute(context))
    asyncio.run(handler.execute(replace(context, call_id="plan-delete-retry")))

    keys = [call["idempotency_key"] for call in runtime.calls]
    assert keys[0] == keys[1]
    assert keys[0] != "model-controlled-key"
    assert context.call_id not in keys[0]
    assert "plan-delete-retry" not in keys[0]
    assert len(runtime.propose_once_calls) == 2
    assert all(call["reuse_existing"] is True for call in runtime.propose_once_calls)


def test_plan_mutate_rejects_a_mismatched_type_hint_for_existing_plan() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="pregnancy")
    handler = PlanMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(plans=[plan]),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    owner_user_id=owner_user_id,
                    tool_name="plan_mutate",
                    args={
                        "operation": "delete",
                        "plan_id": str(plan.id),
                        "plan_type": "milk_management",
                    },
                )
            )
        )

    assert exc_info.value.code == "plan_type_mismatch"


def test_plan_mutate_update_proposes_versioned_metadata_action() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="pregnancy")
    runtime = FakeRuntimeService()
    handler = PlanMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(plans=[plan]),
    )

    result = asyncio.run(
        handler.execute(
            _context(
                owner_user_id=owner_user_id,
                tool_name="plan_mutate",
                args={
                    "operation": "update",
                    "plan_id": str(plan.id),
                    "expected_version": 3,
                    "title": "更新后的孕期计划",
                    "summary": "更新后的摘要",
                },
            )
        )
    )

    assert runtime.calls[-1]["action_type"] == PLAN_UPDATE_ACTION
    assert runtime.calls[-1]["target_id"] == str(plan.id)
    assert runtime.calls[-1]["apply_payload"] == {
        "plan_id": str(plan.id),
        "expected_version": 3,
        "title": "更新后的孕期计划",
        "summary": "更新后的摘要",
    }
    assert result["plan_type"] == "pregnancy"
    assert result["write_succeeded"] is True
    validate_tool_output(
        schema=default_tool_registry().get("plan_mutate").output_schema,
        value=result,
    )


def test_plan_mutate_update_uses_runtime_owned_stable_idempotency() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="pregnancy")
    runtime = FakeRuntimeService()
    handler = PlanMutateToolHandler(
        runtime_service=runtime,
        plans_service=FakePlansService(plans=[plan]),
    )
    context = _context(
        owner_user_id=owner_user_id,
        tool_name="plan_mutate",
        args={
            "operation": "update",
            "plan_id": str(plan.id),
            "expected_version": 3,
            "title": "更新后的孕期计划",
            "idempotency_key": "model-controlled-key",
        },
    )

    asyncio.run(handler.execute(context))
    asyncio.run(handler.execute(replace(context, call_id="plan-update-retry")))

    keys = [call["idempotency_key"] for call in runtime.calls]
    assert keys[0] == keys[1]
    assert keys[0] != "model-controlled-key"
    assert context.call_id not in keys[0]
    assert "plan-update-retry" not in keys[0]
    assert len(runtime.propose_once_calls) == 2
    assert all(call["reuse_existing"] is True for call in runtime.propose_once_calls)


class FakePlansService:
    def __init__(self, *, plans: list | None = None) -> None:
        self.plans = plans or []
        self.list_kwargs: dict = {}

    async def list_plans(self, **kwargs):
        self.list_kwargs = kwargs
        plan_type = kwargs.get("plan_type") or ""
        return [plan for plan in self.plans if not plan_type or plan.plan_type == plan_type]

    async def get_plan(self, *, owner_user_id, plan_id):
        return next(
            plan
            for plan in self.plans
            if plan.id == plan_id and plan.owner_user_id == owner_user_id
        )


class CapturingPlanOperationHandler:
    def __init__(self, *, action_type: str) -> None:
        self.action_type = action_type
        self.calls: list[ToolHandlerContext] = []

    async def execute(self, context: ToolHandlerContext):
        self.calls.append(context)
        return {
            "status": "operation_applied",
            "action_id": str(uuid4()),
            "action_type": self.action_type,
            "action_status": "applied",
            "requires_confirmation": False,
            "confirmation_policy": "explicit_intent",
            "user_visible": False,
            "write_succeeded": True,
            "preview_payload": {},
        }


class FakeRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.propose_once_calls: list[dict] = []

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

    async def propose_action_once(self, **kwargs):
        self.propose_once_calls.append(kwargs)
        return await self.propose_action(**kwargs), True


def _plan(
    *,
    owner_user_id,
    plan_type: str,
    payload: dict | None = None,
):
    timestamp = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        owner_user_id=owner_user_id,
        plan_type=plan_type,
        title="测试计划",
        summary="测试摘要",
        status="active",
        source="agent_action",
        payload=payload or {},
        version=3,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _context(
    *,
    args: dict,
    owner_user_id=None,
    tool_name: str = "plan_mutate",
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=CurrentUser(
            user_id=owner_user_id or uuid4(),
            subject="plan-test-user",
            session_id="plan-session",
            token_id="plan-token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name=tool_name,
        call_id="plan-call",
        args=args,
        thread_id=uuid4(),
    )
