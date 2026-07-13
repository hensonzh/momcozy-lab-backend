import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from production_backend.scripts.run_agent_seed_eval import run_seed_eval


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_run_agent_seed_eval_requires_observed_trace_by_default(tmp_path: Path) -> None:
    output_path = tmp_path / "seed-report.json"
    junit_output_path = tmp_path / "seed-report.xml"

    report = run_seed_eval(cases_path=PRODUCT_AGENT_EVAL_SEED, output_path=output_path, junit_output_path=junit_output_path)

    assert report["total"] >= 20
    assert report["passed"] == 0
    assert report["failed"] == report["total"]
    assert {failure["category"] for result in report["results"] for failure in result["failures"]} == {"missing_trace"}
    assert json.loads(output_path.read_text()) == report
    junit = ElementTree.parse(junit_output_path).getroot()
    assert junit.attrib["tests"] == str(report["total"])
    assert junit.attrib["failures"] == str(report["total"])


def test_run_agent_seed_eval_accepts_runtime_replay_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_eval_observed_trace.v1",
                "traces": [
                    {
                        "suite": "memory_preference_capture",
                        "name": "explicit communication preference stays off the live run path",
                        "provenance": {
                            "capture_mode": "runtime_replay",
                            "run_id": "00000000-0000-4000-8000-000000000001",
                        },
                        "trace": {
                            "tool_calls": [],
                            "events": [{"type": "message.completed"}],
                            "actions": [],
                            "safety_decision": "allow",
                            "final_text": "好的，之后我会先说重点。",
                            "service_skill_id": "cozymate_service_agent",
                        },
                    }
                ],
            }
        )
    )

    report = run_seed_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        trace_fixtures_path=trace_path,
        suite="memory_preference_capture",
    )

    assert report["failed"] == 0
    assert report["passed"] == 1


def test_run_agent_seed_eval_reports_forbidden_observed_tool(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_eval_observed_trace.v1",
                "traces": [
                    {
                        "suite": "memory_sensitive_rejection",
                        "name": "sensitive health fact is not stored as long term memory",
                        "provenance": {
                            "capture_mode": "runtime_replay",
                            "run_id": "00000000-0000-4000-8000-000000000002",
                        },
                        "trace": {
                            "tool_calls": [{"tool_name": "profile_update", "status": "completed"}],
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
        junit_output_path=tmp_path / "failed.xml",
    )

    assert report["failed"] == 1
    assert report["results"][0]["failures"][0]["category"] == "forbidden_tool"
    failure = ElementTree.parse(tmp_path / "failed.xml").getroot().find("testcase/failure")
    assert failure is not None
    assert failure.attrib["type"] == "forbidden_tool"


def test_run_agent_seed_eval_rejects_synthetic_trace_fixture(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_eval_observed_trace.v1",
                "traces": [
                    {
                        "suite": "memory_preference_capture",
                        "name": "explicit communication preference stays off the live run path",
                        "provenance": {
                            "capture_mode": "synthetic_expected",
                            "run_id": "00000000-0000-4000-8000-000000000003",
                        },
                        "trace": {},
                    }
                ],
            }
        )
    )

    with pytest.raises(ValueError, match="capture_mode"):
        run_seed_eval(cases_path=PRODUCT_AGENT_EVAL_SEED, trace_fixtures_path=trace_path)


def test_run_agent_seed_eval_missing_trace_is_always_blocking() -> None:
    report = run_seed_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="memory_preference_capture",
    )

    assert report["failed"] == 1
    assert report["results"][0]["failures"][0]["category"] == "missing_trace"


def test_ci_and_backend_smoke_run_the_observed_runtime_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "production-backend-ci.yml").read_text()
    makefile = (ROOT / "Makefile").read_text()
    observed_command = "python -m pytest -q production_backend/tests/test_agent_task8_observed_eval.py"

    assert observed_command in workflow
    assert "run_agent_seed_eval.py" not in workflow
    backend_smoke = makefile.split("backend-smoke:", 1)[1].split("\n\n", 1)[0]
    assert "production_backend/tests/test_agent_task8_observed_eval.py" in backend_smoke
    assert "run_agent_seed_eval.py" not in backend_smoke
