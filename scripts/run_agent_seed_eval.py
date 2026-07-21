from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.cozymate.evals import (  # noqa: E402
    create_cozymate_eval_assertion_engine,
    load_product_agent_eval_seed_cases,
)
from app.agent_runtime.evals.service import (  # noqa: E402
    AgentEvalFailure,
    AgentEvalRunResult,
    AgentEvalTrace,
)

DEFAULT_CASES = Path("fixtures/agent_eval_cases/product_service_seed.json")


def run_seed_eval(
    *,
    cases_path: Path,
    trace_fixtures_path: Path | None = None,
    suite: str | None = None,
    output_path: Path | None = None,
    junit_output_path: Path | None = None,
) -> dict[str, Any]:
    cases = load_product_agent_eval_seed_cases(cases_path)
    if suite:
        cases = [case for case in cases if case.get("suite") == suite]
    traces = _load_trace_fixtures(trace_fixtures_path)
    engine = create_cozymate_eval_assertion_engine()
    results: list[dict[str, Any]] = []
    for case in cases:
        trace = traces.get(_case_key(case))
        if trace is None:
            result = _missing_trace_result(case)
        else:
            result = engine.evaluate(case=case, trace=trace)
        results.append(_result_payload(result))

    report = {
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if junit_output_path is not None:
        _write_junit_report(report=report, output_path=junit_output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic product agent seed eval assertions.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--trace-fixtures", type=Path, default=None)
    parser.add_argument("--suite", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--junit-output", type=Path, default=None)
    args = parser.parse_args()

    report = run_seed_eval(
        cases_path=args.cases,
        trace_fixtures_path=args.trace_fixtures,
        suite=args.suite,
        output_path=args.output,
        junit_output_path=args.junit_output,
    )
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["failed"] == 0 else 1)


def _load_trace_fixtures(path: Path | None) -> dict[tuple[str, str], AgentEvalTrace]:
    if path is None:
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != "agent_eval_observed_trace.v1":
        raise ValueError("Trace fixtures must use schema_version agent_eval_observed_trace.v1.")
    items = payload.get("traces") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Trace fixtures must be a list or an object with a traces list.")
    traces: dict[tuple[str, str], AgentEvalTrace] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"Trace fixture {index} must be an object.")
        suite = str(item.get("suite") or "").strip()
        name = str(item.get("name") or "").strip()
        provenance = item.get("provenance")
        trace_payload = item.get("trace")
        if not suite or not name or not isinstance(trace_payload, dict):
            raise ValueError(f"Trace fixture {index} must include suite, name, and trace object.")
        if not isinstance(provenance, dict):
            raise ValueError(f"Trace fixture {index} must include provenance.")
        capture_mode = str(provenance.get("capture_mode") or "").strip()
        if capture_mode not in {"runtime_replay", "provider_live"}:
            raise ValueError(f"Trace fixture {index} capture_mode must be runtime_replay or provider_live.")
        if not str(provenance.get("run_id") or "").strip():
            raise ValueError(f"Trace fixture {index} provenance must include run_id.")
        key = (suite, name)
        if key in traces:
            raise ValueError(f"Trace fixture {index} duplicates {suite}/{name}.")
        traces[key] = _trace_from_payload(trace_payload)
    return traces


def _trace_from_payload(payload: dict[str, Any]) -> AgentEvalTrace:
    return AgentEvalTrace(
        tool_calls=_list_of_dicts(payload.get("tool_calls")),
        events=_list_of_dicts(payload.get("events")),
        actions=_list_of_dicts(payload.get("actions")),
        final_text=str(payload.get("final_text") or ""),
        service_skill_id=str(payload.get("service_skill_id") or ""),
    )


def _missing_trace_result(case: dict[str, Any]) -> AgentEvalRunResult:
    return AgentEvalRunResult(
        suite=str(case.get("suite") or ""),
        name=str(case.get("name") or ""),
        passed=False,
        failures=[
            AgentEvalFailure(
                category="missing_trace",
                assertion="trace.required",
                expected="observed trace fixture",
                observed="<none>",
            )
        ],
    )


def _result_payload(result: AgentEvalRunResult) -> dict[str, Any]:
    return {
        "suite": result.suite,
        "name": result.name,
        "passed": result.passed,
        "failures": [asdict(failure) for failure in result.failures],
    }


def _write_junit_report(*, report: dict[str, Any], output_path: Path) -> None:
    suite = Element(
        "testsuite",
        {
            "name": "agent-seed-eval",
            "tests": str(report["total"]),
            "failures": str(report["failed"]),
        },
    )
    for result in report["results"]:
        testcase = SubElement(
            suite,
            "testcase",
            {
                "classname": str(result["suite"]),
                "name": str(result["name"]),
            },
        )
        for failure in result["failures"]:
            failure_node = SubElement(
                testcase,
                "failure",
                {
                    "type": str(failure["category"]),
                    "message": str(failure["assertion"]),
                },
            )
            failure_node.text = json.dumps(failure, sort_keys=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(tostring(suite, encoding="utf-8", xml_declaration=True))


def _case_key(case: dict[str, Any]) -> tuple[str, str]:
    return str(case.get("suite") or ""), str(case.get("name") or "")


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


if __name__ == "__main__":
    main()
