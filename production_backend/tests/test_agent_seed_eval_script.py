import json
from pathlib import Path

from production_backend.scripts.run_agent_seed_eval import run_seed_eval


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_run_agent_seed_eval_self_checks_all_seed_cases(tmp_path: Path) -> None:
    output_path = tmp_path / "seed-report.json"

    report = run_seed_eval(cases_path=PRODUCT_AGENT_EVAL_SEED, output_path=output_path)

    assert report["total"] >= 20
    assert report["failed"] == 0
    assert report["passed"] == report["total"]
    assert json.loads(output_path.read_text()) == report


def test_run_agent_seed_eval_reports_forbidden_observed_tool(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(
        json.dumps(
            {
                "traces": [
                    {
                        "suite": "memory_sensitive_rejection",
                        "name": "sensitive health fact is not stored as long term memory",
                        "trace": {
                            "tool_calls": [{"tool_name": "memory.create.propose", "status": "failed"}],
                            "safety_decision": "allow",
                        },
                    }
                ]
            }
        )
    )

    report = run_seed_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        trace_fixtures_path=trace_path,
        suite="memory_sensitive_rejection",
    )

    assert report["failed"] == 1
    assert report["results"][0]["failures"][0]["category"] == "forbidden_tool"


def test_run_agent_seed_eval_can_require_trace_fixtures() -> None:
    report = run_seed_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="memory_preference_capture",
        fail_on_missing_trace=True,
    )

    assert report["failed"] == 1
    assert report["results"][0]["failures"][0]["category"] == "missing_trace"
