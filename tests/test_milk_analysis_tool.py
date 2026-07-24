from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.agent_runtime.tools import ToolHandlerContext
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY
from app.agent_runtime.tools.validation import validate_tool_output
from app.agents.cozymate.tools.handlers.milk import MilkAnalysisToolHandler
from app.agents.cozymate.tools.registry import default_tool_registry
from app.core.errors import ApiError
from app.modules.auth import CurrentUser


class _FakeOperationHandler:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[ToolHandlerContext] = []

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        self.calls.append(context)
        return dict(self.output)


def _context(*, args: dict[str, Any]) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=CurrentUser(
            user_id=uuid4(),
            subject="milk-analysis-user",
            session_id="milk-analysis-session",
            token_id="milk-analysis-token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        thread_id=uuid4(),
        tool_name="milk_analysis_manage",
        call_id="call-1",
        args=args,
    )


def _handler(
    *,
    summary: dict[str, Any] | None = None,
    detailed: dict[str, Any] | None = None,
    intake: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> tuple[
    MilkAnalysisToolHandler,
    _FakeOperationHandler,
    _FakeOperationHandler,
    _FakeOperationHandler,
    _FakeOperationHandler,
]:
    summary_handler = _FakeOperationHandler(summary or {"status": {"data_coverage": "ready"}})
    detailed_handler = _FakeOperationHandler(detailed or {"analysis": {"pathway": "observe"}})
    intake_handler = _FakeOperationHandler(
        intake
        or {
            "status": "milk_analysis_intake_collecting",
            "workflow_state_id": str(uuid4()),
            "workflow_phase": "collecting_intake",
            "current_field": "infant_wet_diapers",
            "next_question": "过去 24 小时有几片湿尿布？",
            "progress": {"index": 2, "total": 6, "completed_count": 1, "remaining_count": 5},
            "can_evaluate": False,
        }
    )
    evaluate_handler = _FakeOperationHandler(
        evaluation
        or {
            "status": "milk_analysis_completed",
            "workflow_state_id": str(uuid4()),
            "artifact_id": str(uuid4()),
            "artifact_type": "milk_analysis_card",
            "can_start_plan": True,
            "recommended_direction": "stabilize",
            "reason": "Signals are stable.",
        }
    )
    return (
        MilkAnalysisToolHandler(
            summary_handler=summary_handler,
            detailed_handler=detailed_handler,
            intake_handler=intake_handler,
            evaluate_handler=evaluate_handler,
        ),
        summary_handler,
        detailed_handler,
        intake_handler,
        evaluate_handler,
    )


def test_milk_analysis_contract_has_described_inputs_and_outputs() -> None:
    contract = default_tool_registry().get("milk_analysis_manage")

    assert contract.domain == "lactation_analysis"
    assert contract.effect_scope == "agent_internal"
    assert contract.input_schema["required"] == ["operation"]
    assert contract.input_schema["properties"]["operation"]["enum"] == [
        "review",
        "start_or_resume",
        "answer",
        "evaluate",
    ]
    assert contract.input_schema["properties"]["detail_level"]["description"]
    assert contract.input_schema["properties"]["observed_answers"]["items"]["properties"]["field"]["description"]
    assert contract.output_schema is not None
    assert contract.output_schema["properties"]["operation"]["description"]
    assert contract.output_schema["$defs"]["MilkAnalysisStatus"]["properties"]["data_coverage"]["description"]


def test_milk_analysis_review_routes_summary_and_detailed_reads() -> None:
    handler, summary, detailed, _, _ = _handler(
        summary={
            "window": {"days": 3, "limit": 2, "include_today": True},
            "status": {"data_coverage": "ready", "pumping_trend": "stable", "measured_only": True},
            "counts": {
                "infants": 1,
                "recent_feedings": 2,
                "recent_pumpings": 2,
                "trend_days": 3,
                "days_with_pumping": 3,
                "trend_pumping_count": 6,
            },
            "volumes": {
                "recent_feeding_volume_ml": 120.0,
                "recent_pumped_volume_ml": 160.0,
                "trend_pumped_volume_ml": 480.0,
                "average_daily_pumped_volume_ml": 160.0,
            },
            "latest": {"feeding_at": None, "pumping_at": None},
            "observation_flags": [],
        },
        detailed={"analysis": {"pathway": "observe"}},
    )

    summary_result = asyncio.run(
        handler.execute(
            _context(
                args={
                    "operation": "review",
                    "detail_level": "summary",
                    "days": 3,
                    "limit": 2,
                    "runtime_timezone": "Asia/Shanghai",
                }
            )
        )
    )
    detailed_result = asyncio.run(
        handler.execute(_context(args={"operation": "review", "detail_level": "detailed", "days": 7}))
    )

    assert summary_result["operation"] == "review"
    assert summary_result["status"] == "milk_analysis_review_ready"
    assert summary_result["review"]["detail_level"] == "summary"
    assert summary.calls[0].args == {"days": 3, "limit": 2, "runtime_timezone": "Asia/Shanghai"}
    assert detailed_result["review"] == {
        "detail_level": "detailed",
        "analysis": {"pathway": "observe"},
    }
    assert detailed.calls[0].args == {"days": 7}


@pytest.mark.parametrize(
    ("args", "expected_action"),
    [
        ({"operation": "start_or_resume"}, "start"),
        ({"operation": "start_or_resume", "restart": True}, "reset"),
        (
            {
                "operation": "answer",
                "observed_answers": [
                    {"field": "infant_wet_diapers", "evidence": "24 小时有 7 片湿尿布"}
                ],
                "trusted_current_user_text": "24 小时有 7 片湿尿布",
            },
            "answer",
        ),
    ],
)
def test_milk_analysis_routes_durable_workflow_operations(
    args: dict[str, Any],
    expected_action: str,
) -> None:
    handler, _, _, intake, _ = _handler()

    result = asyncio.run(handler.execute(_context(args=args)))

    assert result["operation"] == args["operation"]
    assert result["workflow"]["workflow_phase"] == "collecting_intake"
    assert intake.calls[0].args["action"] == expected_action
    validate_tool_output(
        schema=default_tool_registry().get("milk_analysis_manage").output_schema,
        value=result,
    )
    if expected_action == "answer":
        assert intake.calls[0].args["trusted_current_user_text"] == "24 小时有 7 片湿尿布"
        assert intake.calls[0].args["observed_answers"] == args["observed_answers"]


def test_milk_analysis_evaluate_normalizes_result_and_preserves_deferred_events() -> None:
    deferred_event = {
        "event_type": "artifact.created",
        "payload": {"artifact_id": str(uuid4())},
    }
    handler, _, _, _, evaluation = _handler(
        evaluation={
            "status": "milk_analysis_completed",
            "workflow_state_id": str(uuid4()),
            "artifact_id": str(uuid4()),
            "artifact_type": "milk_analysis_card",
            "can_start_plan": False,
            "recommended_direction": None,
            "reason": "More data is required.",
            DEFERRED_AGENT_EVENTS_KEY: [deferred_event],
        }
    )

    result = asyncio.run(handler.execute(_context(args={"operation": "evaluate"})))

    assert result["status"] == "milk_analysis_completed"
    assert result["operation"] == "evaluate"
    assert result["evaluation"]["replayed"] is False
    assert result[DEFERRED_AGENT_EVENTS_KEY] == [deferred_event]
    assert DEFERRED_AGENT_EVENTS_KEY not in result["evaluation"]
    assert evaluation.calls[0].args == {}
    output_for_validation = dict(result)
    output_for_validation.pop(DEFERRED_AGENT_EVENTS_KEY)
    validate_tool_output(
        schema=default_tool_registry().get("milk_analysis_manage").output_schema,
        value=output_for_validation,
    )


def test_milk_analysis_rejects_operation_specific_arguments_in_wrong_mode() -> None:
    handler, _, _, _, _ = _handler()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler.execute(
                _context(
                    args={
                        "operation": "review",
                        "restart": True,
                    }
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
