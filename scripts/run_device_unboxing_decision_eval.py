from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import Settings  # noqa: E402
from app.agents.cozymate.evals import (  # noqa: E402
    create_cozymate_eval_assertion_engine,
    load_product_agent_eval_seed_cases,
)
from app.agents.cozymate.device_guidance import (  # noqa: E402
    DeviceGuidanceReferenceService,
)
from app.agents.cozymate.prompts import (  # noqa: E402
    DEFAULT_STABLE_SYSTEM_PROMPT,
)
from app.agents.cozymate.skill_registry import (  # noqa: E402
    default_service_skill_registry,
)
from app.agents.cozymate.tools import (  # noqa: E402
    default_tool_namespace_registry,
    default_tool_registry,
)
from app.agent_runtime.evals.service import (  # noqa: E402
    AgentEvalTrace,
)
from app.agent_runtime.runs.models import AgentWorkflowState  # noqa: E402
from app.agent_runtime.tools.result import ToolResult  # noqa: E402
from app.agents.cozymate.workflows.ongoing_work import (  # noqa: E402
    project_workflow_context,
)
from app.agent_runtime.providers import (  # noqa: E402
    AgentModelRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    SdkToolNamespace,
    create_agent_model_runner,
    sdk_tool_name,
)


DEFAULT_CASES = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


@dataclass(frozen=True)
class DeviceUnboxingDecisionCase:
    suite: str
    previous_assistant_text: str
    user_text: str


DEVICE_UNBOXING_DECISION_CASES = (
    DeviceUnboxingDecisionCase(
        suite="device_unboxing_step_continuation",
        previous_assistant_text="请把当前步骤里的部件全部取出并核对完整，完成后回复继续。",
        user_text="继续",
    ),
    DeviceUnboxingDecisionCase(
        suite="device_unboxing_incomplete_step",
        previous_assistant_text="请把当前步骤里的部件全部取出并核对完整，完成后回复继续。",
        user_text="还没完成，磁吸充电线好像没找到。",
    ),
    DeviceUnboxingDecisionCase(
        suite="device_unboxing_incomplete_delivery",
        previous_assistant_text="第一步先核对包装内的部件。",
        user_text="继续",
    ),
)


async def run_device_unboxing_decision_eval(
    *,
    runner: AgentModelRunner,
    model_name: str,
    cases_path: Path = DEFAULT_CASES,
    output_path: Path | None = None,
    trace_output_path: Path | None = None,
) -> dict[str, Any]:
    seed_cases = {case["suite"]: case for case in load_product_agent_eval_seed_cases(cases_path)}
    missing = [case.suite for case in DEVICE_UNBOXING_DECISION_CASES if case.suite not in seed_cases]
    if missing:
        raise ValueError(f"Device unboxing decision eval is missing seed cases: {', '.join(missing)}")

    assertion_engine = create_cozymate_eval_assertion_engine()
    results: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for decision_case in DEVICE_UNBOXING_DECISION_CASES:
        request = _decision_request(case=decision_case, instructions=DEFAULT_STABLE_SYSTEM_PROMPT)
        model_result = await runner.run_reasoning(request)
        trace = AgentEvalTrace(
            tool_calls=[dict(item) for item in model_result.tool_calls],
            final_text=model_result.final_text,
            service_skill_id="device-guidance",
        )
        eval_result = assertion_engine.evaluate(case=seed_cases[decision_case.suite], trace=trace)
        results.append(
            {
                "suite": eval_result.suite,
                "name": eval_result.name,
                "passed": eval_result.passed,
                "failures": [asdict(failure) for failure in eval_result.failures],
                "observed_tool_calls": trace.tool_calls,
                "final_text": trace.final_text,
            }
        )
        traces.append(
            {
                "suite": eval_result.suite,
                "name": eval_result.name,
                "provenance": {
                    "capture_mode": "provider_live",
                    "run_id": request.run_id,
                    "model": model_name,
                },
                "trace": {
                    "tool_calls": trace.tool_calls,
                    "events": [],
                    "actions": [],
                    "final_text": trace.final_text,
                    "service_skill_id": trace.service_skill_id,
                },
            }
        )

    report = {
        "schema_version": "device_unboxing_decision_eval.v1",
        "execution_mode": "provider_live",
        "model": model_name,
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    _write_json(output_path, report)
    _write_json(
        trace_output_path,
        {
            "schema_version": "agent_eval_observed_trace.v1",
            "traces": traces,
        },
    )
    return report


def _decision_request(
    *,
    case: DeviceUnboxingDecisionCase,
    instructions: str,
) -> SdkNodeRequest:
    run_id = uuid4()
    thread_id = uuid4()
    actor_user_id = uuid4()
    workflow = AgentWorkflowState(
        id=uuid4(),
        thread_id=thread_id,
        owner_user_id=actor_user_id,
        run_id=run_id,
        workflow_type="device_unboxing",
        status="waiting",
        schema_version="device-unboxing.v1",
        state={"phase": "guiding", "device_model": "Air1", "completed_steps": []},
        active_step="guide.parts",
        revision=1,
        step_token="device-unboxing-live-eval",
    )
    workflow_reply = {
        "workflow_state_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "revision": workflow.revision,
        "step_token": workflow.step_token,
    }
    skill = default_service_skill_registry().get("device-guidance")
    reference = DeviceGuidanceReferenceService().read(model="Air1", step="guide.parts")
    model_input = [
        *_historical_tool_result(
            call_id="load-device-guidance",
            tool_name="load_service_skill",
            args={"service_skill_id": skill.service_skill_id},
            output={
                "schema_version": "service_skill_load.v2",
                "service_skill_id": skill.service_skill_id,
                "skill_version": skill.version,
                "skill": {
                    "service_skill_id": skill.service_skill_id,
                    "name": skill.name,
                    "description": skill.description,
                    "instructions": skill.prompt_block(),
                },
                "business_facts": {},
            },
        ),
        *_historical_tool_result(
            call_id="start-device-unboxing",
            tool_name=sdk_tool_name("devices.unboxing.advance"),
            args={"action": "start", "device_model": "Air1"},
            output={
                "status": "unboxing_started",
                "workflow": project_workflow_context([workflow], workflow_reply=workflow_reply)[0],
                "guidance": reference,
            },
        ),
        {"role": "assistant", "content": case.previous_assistant_text},
        {"role": "user", "content": case.user_text},
    ]
    tool_registry = default_tool_registry()
    tool_contract = tool_registry.get("devices.unboxing.advance")
    namespace = default_tool_namespace_registry(tool_registry).get("device_support")
    return SdkNodeRequest(
        run_id=str(run_id),
        thread_id=str(thread_id),
        actor_user_id=str(actor_user_id),
        instructions=instructions,
        model_input=model_input,
        tool_names=(tool_contract.name,),
        tool_namespaces=(
            SdkToolNamespace(
                name=namespace.name,
                description=namespace.description,
                tool_names=(tool_contract.name,),
            ),
        ),
        tools=(
            SdkToolDefinition(
                contract_name=tool_contract.name,
                sdk_name=sdk_tool_name(tool_contract.name),
                description=tool_contract.description,
                params_json_schema=tool_contract.input_schema,
                invoke=_advance_tool_result,
                namespace_name=namespace.name,
            ),
        ),
        trace_id=f"device-unboxing-decision-eval-{run_id}",
        service_skill_id="cozymate_service_agent",
    )


def _historical_tool_result(
    *,
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": tool_name,
            "arguments": json.dumps(args, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": ToolResult.json(output).to_function_call_output(),
        },
    ]


async def _advance_tool_result(args_json: str) -> ToolResult:
    args = json.loads(args_json)
    output = {
        "status": "unboxing_step_advanced",
        "workflow": {
            "device_model": "Air1",
            "phase": "guiding",
            "current_step": "guide.controls",
            "completed_steps": ["guide.parts"],
        },
        "guidance": {
            "current_step": {
                "id": "guide.controls",
                "title": "主机按钮与指示灯",
                "content": "从充电舱中取出主机，认识按钮和电量指示灯。",
            }
        },
    }
    if args.get("action") != "complete_current":
        output = {"status": "unexpected_action", "received_action": args.get("action")}
    return ToolResult.json(output)


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run provider-live device unboxing tool-decision evals.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model", default="")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--trace-output", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for provider-live device unboxing evals.")
    model_name = args.model or settings.openai_model
    runner = create_agent_model_runner(
        settings=settings,
        model=model_name,
        max_turns=3,
        metrics_node_name="device_unboxing_decision_eval",
    )
    report = asyncio.run(
        run_device_unboxing_decision_eval(
            runner=runner,
            model_name=model_name,
            cases_path=args.cases,
            output_path=args.output,
            trace_output_path=args.trace_output,
        )
    )
    if args.output is None:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
