import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.validation import validate_tool_output
from app.agents.cozymate.actions.plans import (
    PLAN_DELETE_ACTION,
    PLAN_UPDATE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
)
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.plans import PlanMutateToolHandler, PlanReadToolHandler
from app.agents.cozymate.tools.policy import CozymateToolExecutionPolicy
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
        PREGNANCY_PLAN_CREATE_ACTION,
        PLAN_UPDATE_ACTION,
        PLAN_DELETE_ACTION,
    )
    variants = mutate_contract.input_schema["anyOf"]
    assert all("operation" in variant["required"] for variant in variants)
    assert [
        variant["properties"]["operation"]["enum"][0]
        for variant in variants
    ] == [
        "create",
        "update",
        "delete",
    ]
    assert variants[0]["properties"]["plan_type"]["enum"] == ["pregnancy"]
    assert "plan_type" not in variants[1]["properties"]
    assert "plan_type" not in variants[2]["properties"]
    assert "include_content" not in read_contract.input_schema["anyOf"][0]["properties"]
    assert "include_content" not in read_contract.input_schema["anyOf"][1]["properties"]
    assert read_contract.input_schema["anyOf"][0]["properties"]["limit"]["maximum"] == 20
    assert "confirmation_evidence" in variants[2]["required"]
    for schema in (read_contract.input_schema, mutate_contract.input_schema):
        for variant in schema["anyOf"]:
            for field_name, field_schema in variant["properties"].items():
                assert field_schema.get("description"), f"{field_name} lacks a description"


def test_plan_read_lists_owner_scoped_plan_metadata_without_expanding_content() -> None:
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
                args={
                    "mode": "list",
                    "runtime_local_date": "2026-07-27",
                },
            )
        )
    )

    assert service.list_kwargs == {
        "owner_user_id": owner_user_id,
        "plan_type": "",
        "status": "active",
        "as_of_date": date(2026, 7, 27),
        "limit": 20,
    }
    assert result["mode"] == "list"
    assert result["count"] == 2
    assert result["plans"][0]["content"] == {}
    assert result["plans"][1]["content"] == {}
    assert "must-not-leak" not in str(result)
    validate_tool_output(
        schema=default_tool_registry().get("plan_read").output_schema,
        value=result,
    )


def test_plan_read_detail_requires_plan_id_and_returns_version_for_updates() -> None:
    owner_user_id = uuid4()
    plan = _plan(
        owner_user_id=owner_user_id,
        plan_type="pregnancy",
        payload={
            "card": {
                "card_type": "birth_journey_plan_card",
                "card_json": {"title": "孕期计划"},
            },
            "plan_context": {"medical_notes": "must-not-leak"},
        },
    )
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
    assert result["plan"]["content"] == {
        "card": {
            "card_type": "birth_journey_plan_card",
            "card_json": {"title": "孕期计划"},
        },
    }
    assert "must-not-leak" not in str(result)

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


def test_plan_mutate_routes_pregnancy_create() -> None:
    pregnancy = CapturingPlanOperationHandler(action_type=PREGNANCY_PLAN_CREATE_ACTION)
    handler = PlanMutateToolHandler(
        runtime_service=FakeRuntimeService(),
        plans_service=FakePlansService(),
        pregnancy_create_handler=pregnancy,
    )

    result = asyncio.run(
        handler.execute(
            _context(
                tool_name="plan_mutate",
                args={"operation": "create", "plan_type": "pregnancy"},
            )
        )
    )

    assert pregnancy.calls
    assert result["operation"] == "create"
    assert result["plan_type"] == "pregnancy"
    assert result["action_type"] == PREGNANCY_PLAN_CREATE_ACTION
    assert result["status"] == "applied"
    assert result["result_code"] == "action_applied"
    assert result["plan_id"] == str(pregnancy.resource_id)
    assert result["plan_version"] == 1


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
                    args={"operation": "create", "plan_type": "milk_management"},
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
            "confirmation_evidence": "请删除这个计划",
            "trusted_current_user_text": "请删除这个计划",
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


def test_plan_mutate_delete_rejects_confirmation_evidence_not_grounded_in_current_message() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="milk_management")
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
                    "operation": "delete",
                    "plan_id": str(plan.id),
                    "confirmation_evidence": "请删除这个计划",
                    "trusted_current_user_text": "先让我看看这个计划",
                },
            )
        )
    )

    assert result["status"] == "input_required"
    assert result["result_code"] == "needs_plan_delete_confirmation"
    assert result["write_succeeded"] is False
    assert runtime.calls == []


def test_plan_delete_confirmation_evidence_is_not_persisted_in_safe_args() -> None:
    policy = CozymateToolExecutionPolicy()

    safe_args = policy.safe_args(
        tool_name="plan_mutate",
        args={
            "operation": "delete",
            "plan_id": "10000000-0000-4000-8000-000000000001",
            "confirmation_evidence": "请删除这个计划",
        },
    )

    assert safe_args == {
        "operation": "delete",
        "plan_id": "10000000-0000-4000-8000-000000000001",
    }


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
    assert result["status"] == "applied"
    assert result["result_code"] == "action_applied"
    assert result["plan_id"] == str(plan.id)
    assert result["plan_version"] == 4
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
        self.resource_id = uuid4()
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
            "resource_type": "plan",
            "resource_id": str(self.resource_id),
            "result_details": {"version": 1},
        }


class FakeRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.propose_once_calls: list[dict] = []

    async def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        action = AgentAction(
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
        action.result_payload = {
            "resource_type": "plan",
            "resource_id": kwargs.get("target_id", ""),
            "details": {"version": 4},
        }
        return action

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
