import asyncio
from uuid import uuid4

import pytest

from app.agent_runtime.tools.executor import ToolHandlerContext
from app.core.errors import ApiError
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.pregnancy_plan import PregnancyPlanWorkflowToolHandler
from app.agents.cozymate.tools.policy import CozymateToolExecutionPolicy
from app.modules.auth import CurrentUser


PREGNANCY_PLAN_WORKFLOW_TOOL = "pregnancy_plan_manage"
LEGACY_PREGNANCY_PLAN_TOOLS = {
    "pregnancy_plan_intake_start",
    "pregnancy_plan_intake_analyze",
    "pregnancy_plan_intake_advance",
    "pregnancy_plan_propose",
}


def test_pregnancy_plan_uses_one_model_visible_workflow_contract() -> None:
    registry = default_tool_registry()
    names = set(registry.names_for_sdk())

    assert PREGNANCY_PLAN_WORKFLOW_TOOL in names
    assert LEGACY_PREGNANCY_PLAN_TOOLS.isdisjoint(names)

    contract = registry.get(PREGNANCY_PLAN_WORKFLOW_TOOL)
    assert contract.domain == "birth_prep"
    assert contract.effect_scope == "user_resource"
    assert contract.action_types == ("pregnancy.plan.create",)
    assert contract.blocking_policy == "must_wait"
    assert contract.result_dependency == "final_response"


def test_pregnancy_plan_manage_contract_exposes_state_machine_commands() -> None:
    schema = default_tool_registry().get(PREGNANCY_PLAN_WORKFLOW_TOOL).input_schema
    command = schema["properties"]["command"]

    assert schema["required"] == ["command"]
    assert set(command["enum"]) == {
        "start_or_resume",
        "submit_form",
        "answer_current",
        "edit_answer",
        "pause",
        "resume",
        "abandon",
        "generate_plan",
    }
    assert {"choice_id", "answer", "step_id", "restart"} <= set(schema["properties"])


def test_pregnancy_plan_manage_is_the_only_model_visible_intake_contract() -> None:
    names = set(default_tool_registry().names_for_sdk())

    assert PREGNANCY_PLAN_WORKFLOW_TOOL in names
    assert LEGACY_PREGNANCY_PLAN_TOOLS.isdisjoint(names)
    assert "pregnancy_plan_todo_propose" not in names


def test_pregnancy_plan_manage_effect_is_dynamic_but_action_binding_remains_static() -> None:
    policy = CozymateToolExecutionPolicy()

    for command in {
        "start_or_resume",
        "submit_form",
        "answer_current",
        "edit_answer",
        "pause",
        "resume",
        "abandon",
    }:
        assert (
            policy.effective_effect_scope(
                tool_name=PREGNANCY_PLAN_WORKFLOW_TOOL,
                args={"command": command},
                default="user_resource",
            )
            == "agent_internal"
        )

    assert (
        policy.effective_effect_scope(
            tool_name=PREGNANCY_PLAN_WORKFLOW_TOOL,
            args={"command": "generate_plan"},
            default="user_resource",
        )
        == "user_resource"
    )


@pytest.mark.parametrize(
    ("command", "expected_delegate"),
    [
        ("start_or_resume", "start"),
        ("submit_form", "analyze"),
        ("answer_current", "advance"),
        ("abandon", "advance"),
        ("generate_plan", "generate"),
    ],
)
def test_pregnancy_plan_manage_facade_dispatches_to_internal_operations(
    command: str,
    expected_delegate: str,
) -> None:
    calls: list[tuple[str, dict]] = []
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=object(),
        start_handler=_SpyDelegate("start", calls),
        analyze_handler=_SpyDelegate("analyze", calls),
        advance_handler=_SpyDelegate("advance", calls),
        generate_handler=_SpyDelegate("generate", calls),
    )
    args = {
        "command": command,
        "choice_id": "confirm_no_checkup_yet",
        "answer": "还没有",
        "runtime_workflow_context": {"phase": "checkup_done_question"},
    }
    if command == "start_or_resume":
        args.pop("runtime_workflow_context")

    result = asyncio.run(handler.execute(_context(args)))

    assert result == {"delegate": expected_delegate}
    assert calls[0][0] == expected_delegate
    delegated_args = calls[0][1]
    assert "command" not in delegated_args
    if command == "answer_current":
        assert delegated_args["action"] == "confirm_no_checkup_yet"
    if command == "abandon":
        assert delegated_args["action"] == "abandon"


def test_pregnancy_plan_manage_facade_rejects_a_choice_not_valid_for_current_phase() -> None:
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=object(),
        start_handler=_SpyDelegate("start", []),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    {
                        "command": "answer_current",
                        "choice_id": "skip_checkup_records",
                        "runtime_workflow_context": {"phase": "checkup_done_question"},
                    }
                )
            )
        )

    assert exc_info.value.code == "invalid_pregnancy_plan_choice"


def test_pregnancy_plan_manage_facade_pauses_and_resumes_without_advancing() -> None:
    runtime = _WorkflowRuntime()
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=runtime,
        start_handler=_SpyDelegate("start", []),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )
    active = {
        "phase": "checkup_records_upload",
        "visible_question": "请上传产检记录，或跳过。",
        "plan_context": {"current_week": "20周"},
    }

    paused_result = asyncio.run(
        handler.execute(_context({"command": "pause", "runtime_workflow_context": active}))
    )
    paused = runtime.persisted[-1]
    resumed_result = asyncio.run(
        handler.execute(_context({"command": "resume", "runtime_workflow_context": paused}))
    )

    assert paused_result.audit_output["status"] == "pregnancy_plan_workflow_paused"
    assert paused["paused"] is True
    assert resumed_result.audit_output["status"] == "pregnancy_plan_workflow_resumed"
    assert runtime.persisted[-1]["phase"] == "checkup_records_upload"
    assert "paused" not in runtime.persisted[-1]


def test_pregnancy_plan_manage_facade_resumes_a_safety_pause_without_restarting() -> None:
    runtime = _WorkflowRuntime()
    start_calls: list[tuple[str, dict]] = []
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=runtime,
        start_handler=_SpyDelegate("start", start_calls),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )
    interrupted = {
        "phase": "checkup_records_upload",
        "resume_phase": "checkup_records_upload",
        "paused": True,
        "interrupted_by_safety_signal": True,
        "safety_signal_ids": ["heavy_bleeding"],
        "plan_context": {"current_week": "32周"},
    }

    result = asyncio.run(
        handler.execute(
            _context(
                {
                    "command": "start_or_resume",
                    "runtime_workflow_context": interrupted,
                }
            )
        )
    )

    assert result.audit_output["status"] == "pregnancy_plan_workflow_resumed"
    assert start_calls == []
    assert runtime.persisted[-1]["phase"] == "checkup_records_upload"
    assert "paused" not in runtime.persisted[-1]
    assert "interrupted_by_safety_signal" not in runtime.persisted[-1]
    assert runtime.persisted[-1]["safety_signal_ids"] == ["heavy_bleeding"]


def test_pregnancy_plan_manage_facade_honors_an_explicit_restart() -> None:
    start_calls: list[tuple[str, dict]] = []
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=object(),
        start_handler=_SpyDelegate("start", start_calls),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )

    result = asyncio.run(
        handler.execute(
            _context(
                {
                    "command": "start_or_resume",
                    "restart": True,
                    "runtime_workflow_context": {
                        "phase": "checkup_records_upload",
                        "source_form_submission_id": "submission-1",
                    },
                }
            )
        )
    )

    assert result == {"delegate": "start"}
    assert start_calls[0][1]["restart"] is True


def test_pregnancy_plan_manage_facade_delegates_collecting_intake_resume_to_form_handler() -> None:
    start_calls: list[tuple[str, dict]] = []
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=_WorkflowRuntime(),
        start_handler=_SpyDelegate("start", start_calls),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )
    workflow = {
        "phase": "collecting_intake",
        "source_form_artifact_id": str(uuid4()),
    }

    result = asyncio.run(
        handler.execute(
            _context(
                {
                    "command": "start_or_resume",
                    "runtime_workflow_context": workflow,
                }
            )
        )
    )

    assert result == {"delegate": "start"}
    assert start_calls[0][1]["runtime_workflow_context"] == workflow


def test_pregnancy_plan_manage_facade_reopens_the_form_for_basic_info_edit() -> None:
    calls: list[tuple[str, dict]] = []
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=object(),
        start_handler=_SpyDelegate("start", calls),
        analyze_handler=_SpyDelegate("analyze", calls),
        advance_handler=_SpyDelegate("advance", calls),
        generate_handler=_SpyDelegate("generate", calls),
    )
    prior = {
        "phase": "final_plan_confirmation",
        "source_form_submission_id": "submission-1",
        "plan_context": {"current_week": "28周", "age": 32},
    }

    asyncio.run(
        handler.execute(
            _context(
                {
                    "command": "edit_answer",
                    "step_id": "basic_intake",
                    "runtime_workflow_context": prior,
                }
            )
        )
    )

    assert calls[0][0] == "start"
    assert calls[0][1]["restart"] is True
    assert calls[0][1]["default_values"] == {"current_week": "28周", "age": 32}
    assert calls[0][1]["prior_workflow_context"] == prior


def test_pregnancy_plan_manage_facade_interrupts_an_urgent_historical_text_edit() -> None:
    runtime = _WorkflowRuntime()
    handler = PregnancyPlanWorkflowToolHandler(
        runtime_service=runtime,
        start_handler=_SpyDelegate("start", []),
        analyze_handler=_SpyDelegate("analyze", []),
        advance_handler=_SpyDelegate("advance", []),
        generate_handler=_SpyDelegate("generate", []),
    )
    active = {
        "phase": "ready_to_generate",
        "final_plan_confirmed": True,
        "plan_context": {"final_additional_info": "没有其他补充"},
    }

    result = asyncio.run(
        handler.execute(
            _context(
                {
                    "command": "edit_answer",
                    "step_id": "final_confirmation",
                    "choice_id": "submit_final_additional_info",
                    "answer": "我现在大量出血",
                    "runtime_workflow_context": active,
                }
            )
        )
    )

    assert result.audit_output["status"] == "urgent_care_required"
    assert result.audit_output["blocks_plan_flow"] is True
    interrupted = runtime.persisted[-1]
    assert interrupted["phase"] == "ready_to_generate"
    assert interrupted["resume_phase"] == "ready_to_generate"
    assert interrupted["paused"] is True
    assert interrupted["interrupted_by_safety_signal"] is True
    assert interrupted["safety_signal_ids"] == ["heavy_bleeding"]
    assert interrupted["plan_context"]["final_additional_info"] == "没有其他补充"


class _SpyDelegate:
    def __init__(self, name: str, calls: list[tuple[str, dict]]) -> None:
        self.name = name
        self.calls = calls

    async def execute(self, context: ToolHandlerContext) -> dict:
        self.calls.append((self.name, dict(context.args)))
        return {"delegate": self.name}


class _WorkflowRuntime:
    def __init__(self) -> None:
        self.persisted: list[dict] = []

    async def upsert_workflow_state(self, **kwargs):
        self.persisted.append(dict(kwargs["state"]))
        return object()


def _context(args: dict) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=CurrentUser(
            user_id=uuid4(),
            subject="test-user",
            session_id="test-session",
            token_id="test-token",
            roles=frozenset({"user"}),
            permissions=frozenset({"plans:write:self"}),
        ),
        run_id=uuid4(),
        tool_name=PREGNANCY_PLAN_WORKFLOW_TOOL,
        call_id="call-1",
        args=args,
        thread_id=uuid4(),
    )
