import asyncio
import json
from pathlib import Path

from app.agent_runtime.providers import SdkNodeResult
from scripts.run_device_unboxing_decision_eval import (
    DEVICE_UNBOXING_DECISION_CASES,
    run_device_unboxing_decision_eval,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_device_unboxing_live_eval_uses_model_decisions_and_writes_provider_traces(tmp_path: Path) -> None:
    runner = RecordingDecisionRunner(
        results=[
            SdkNodeResult(
                final_text="下一步认识主机按键。",
                tool_calls=[
                    {
                        "tool_name": "devices_guidance_manage",
                        "status": "completed",
                        "args": {"model": "Air1", "operation": "complete_current"},
                    }
                ],
            ),
            SdkNodeResult(final_text="先一起确认缺少的配件。"),
            SdkNodeResult(final_text="先把当前步骤的完整清点内容补充给你。"),
        ]
    )
    report_path = tmp_path / "report.json"
    trace_path = tmp_path / "traces.json"

    report = asyncio.run(
        run_device_unboxing_decision_eval(
            runner=runner,
            model_name="provider-model",
            cases_path=PRODUCT_AGENT_EVAL_SEED,
            output_path=report_path,
            trace_output_path=trace_path,
        )
    )

    assert report["execution_mode"] == "provider_live"
    assert report["total"] == 3
    assert report["passed"] == 3
    assert report["failed"] == 0
    assert len(runner.requests) == 3
    assert json.loads(report_path.read_text()) == report

    model_input = runner.requests[0].model_input
    assert [item.get("type") or item.get("role") for item in model_input] == [
        "function_call",
        "function_call_output",
        "assistant",
        "user",
    ]
    assert "每轮给 1 个主步骤" in runner.requests[0].instructions
    start_result = json.loads(model_input[1]["output"])
    assert start_result["workflow"]["current_message_relation"] == "reply_to_current_step"
    assert "completion_confirmation" not in json.dumps(start_result)
    assert runner.requests[0].model_input[-1] == {"role": "user", "content": "继续"}
    assert runner.requests[2].model_input[-2]["content"] == "第一步先核对包装内的部件。"

    traces = json.loads(trace_path.read_text())
    assert traces["schema_version"] == "agent_eval_observed_trace.v1"
    assert {item["provenance"]["capture_mode"] for item in traces["traces"]} == {"provider_live"}
    assert {item["suite"] for item in traces["traces"]} == {case.suite for case in DEVICE_UNBOXING_DECISION_CASES}


def test_device_unboxing_live_eval_fails_when_model_does_not_choose_required_advance() -> None:
    runner = RecordingDecisionRunner(results=[SdkNodeResult(final_text="我再重复一下。") for _case in DEVICE_UNBOXING_DECISION_CASES])

    report = asyncio.run(
        run_device_unboxing_decision_eval(
            runner=runner,
            model_name="provider-model",
            cases_path=PRODUCT_AGENT_EVAL_SEED,
        )
    )

    assert report["failed"] == 1
    failed = next(item for item in report["results"] if not item["passed"])
    assert failed["suite"] == "device_unboxing_step_continuation"
    assert failed["failures"][0]["category"] == "missing_tool"


def test_device_unboxing_provider_live_eval_is_exposed_as_an_explicit_command() -> None:
    makefile = (ROOT / "Makefile").read_text()
    scripts_readme = (ROOT / "scripts" / "README.md").read_text()
    command = "scripts/run_device_unboxing_decision_eval.py"

    assert "backend-agent-device-decision-eval:" in makefile
    assert command in makefile
    assert command in scripts_readme
    assert "provider-live" in scripts_readme


class RecordingDecisionRunner:
    def __init__(self, *, results: list[SdkNodeResult]) -> None:
        self.results = list(results)
        self.requests = []

    async def run_reasoning(self, request):
        self.requests.append(request)
        return self.results.pop(0)

    def supports_web_search(self) -> bool:
        return False
