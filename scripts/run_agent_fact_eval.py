from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from xml.etree.ElementTree import Element, SubElement, tostring


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.agent_runtime.facts.extraction import (  # noqa: E402
    AgentFactExtractor,
    FactExtractionContext,
    fact_inputs_to_candidate_payload,
)
from app.modules.agent_runtime.sdk import (  # noqa: E402
    OpenAIResponsesRunner,
    ScriptedSdkBackend,
    scripted_sdk_response,
)


FACT_EVAL_SCHEMA_VERSION = "agent_fact_eval.v1"
DEFAULT_CASES = ROOT / "fixtures" / "agent_eval_cases" / "user_fact_extraction_seed.json"
ALLOWED_CASE_STATUSES = frozenset({"active", "draft", "deprecated"})
ALLOWED_PRIORITIES = frozenset({"p0", "p1", "p2", "p3"})


def load_fact_eval_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != FACT_EVAL_SCHEMA_VERSION:
        raise ValueError(f"Fact eval schema_version must be {FACT_EVAL_SCHEMA_VERSION}.")
    suite = _required_string(payload.get("suite"), field_name="suite")
    defaults = payload.get("defaults")
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError("Fact eval defaults must be an object.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Fact eval must include a non-empty cases list.")

    normalized: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for index, item in enumerate(cases):
        if not isinstance(item, dict):
            raise ValueError(f"Fact eval case {index} must be an object.")
        case = {**defaults, **item, "suite": suite}
        case_id = _required_string(case.get("id"), field_name=f"cases[{index}].id")
        if case_id in case_ids:
            raise ValueError(f"Fact eval case id must be unique: {case_id}.")
        case_ids.add(case_id)
        _validate_case(case=case, index=index)
        normalized.append(case)
    return normalized


async def run_fact_extraction_eval(
    *,
    cases_path: Path = DEFAULT_CASES,
    case_ids: set[str] | None = None,
    output_path: Path | None = None,
    junit_output_path: Path | None = None,
) -> dict[str, Any]:
    loaded_cases = load_fact_eval_cases(cases_path)
    suite = str(loaded_cases[0]["suite"])
    cases = [case for case in loaded_cases if case["status"] == "active"]
    if case_ids:
        cases = [case for case in cases if case["id"] in case_ids]
        missing_ids = case_ids - {str(case["id"]) for case in cases}
        if missing_ids:
            raise ValueError(f"Unknown active fact eval case ids: {', '.join(sorted(missing_ids))}.")

    results = [await _run_case(case) for case in cases]
    report: dict[str, Any] = {
        "schema_version": FACT_EVAL_SCHEMA_VERSION,
        "suite": suite,
        "execution_mode": "scripted_backend_observed_extraction",
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if junit_output_path is not None:
        _write_junit_report(report=report, output_path=junit_output_path)
    return report


async def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    model_output = case["scripted_model_output"]
    backend = ScriptedSdkBackend(
        [scripted_sdk_response(final_text=json.dumps(model_output, ensure_ascii=False, sort_keys=True))]
    )
    extractor = AgentFactExtractor(model_runner=OpenAIResponsesRunner(backend=backend))
    observed_inputs = await extractor.extract(
        context=FactExtractionContext(
            owner_user_id=uuid5(NAMESPACE_URL, f"{case['id']}:owner"),
            run_id=uuid5(NAMESPACE_URL, f"{case['id']}:run"),
            source_message_id=uuid5(NAMESPACE_URL, f"{case['id']}:message"),
            source_text=str(case["source_user_message"]),
            recent_dialogue=tuple(
                (str(turn["role"]), str(turn["content"])) for turn in case["recent_dialogue"]
            ),
            observed_at=datetime.fromisoformat(str(case["observed_at"])),
        )
    )
    observed_facts = _sorted_facts(fact_inputs_to_candidate_payload(observed_inputs))
    expected_facts = _sorted_facts(case["expected"]["facts"])
    failures: list[dict[str, str]] = []
    if observed_facts != expected_facts:
        failures.append(
            {
                "category": "fact_extraction_mismatch",
                "assertion": "facts.exact",
                "expected": json.dumps(expected_facts, ensure_ascii=False, sort_keys=True),
                "observed": json.dumps(observed_facts, ensure_ascii=False, sort_keys=True),
            }
        )
    request = backend.requests[0] if backend.requests else None
    return {
        "id": case["id"],
        "scenario": case["scenario"],
        "priority": case["priority"],
        "passed": not failures,
        "failures": failures,
        "observed_facts": observed_facts,
        "scripted_candidate_count": len(model_output["facts"]),
        "extractor_requests": len(backend.requests),
        "prompt_version": request.prompt_version if request is not None else "",
    }


def _validate_case(*, case: dict[str, Any], index: int) -> None:
    for field in ("scenario", "source_user_message", "observed_at"):
        _required_string(case.get(field), field_name=f"cases[{index}].{field}")
    priority = _required_string(case.get("priority"), field_name=f"cases[{index}].priority")
    if priority not in ALLOWED_PRIORITIES:
        raise ValueError(f"Fact eval case {index} has unsupported priority: {priority}.")
    status = _required_string(case.get("status"), field_name=f"cases[{index}].status")
    if status not in ALLOWED_CASE_STATUSES:
        raise ValueError(f"Fact eval case {index} has unsupported status: {status}.")
    try:
        observed_at = datetime.fromisoformat(str(case["observed_at"]))
    except ValueError as exc:
        raise ValueError(f"Fact eval case {index} observed_at must be ISO-8601.") from exc
    if observed_at.tzinfo is None:
        raise ValueError(f"Fact eval case {index} observed_at must include a timezone.")
    recent_dialogue = case.get("recent_dialogue")
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        raise ValueError(f"Fact eval case {index} recent_dialogue must be a non-empty list.")
    for turn_index, turn in enumerate(recent_dialogue):
        if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"}:
            raise ValueError(f"Fact eval case {index} dialogue turn {turn_index} has an invalid role.")
        _required_string(turn.get("content"), field_name=f"cases[{index}].recent_dialogue[{turn_index}].content")
    model_output = case.get("scripted_model_output")
    if not isinstance(model_output, dict) or not isinstance(model_output.get("facts"), list):
        raise ValueError(f"Fact eval case {index} scripted_model_output.facts must be a list.")
    expected = case.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get("facts"), list):
        raise ValueError(f"Fact eval case {index} expected.facts must be a list.")
    _validate_fact_list(expected["facts"], field_name=f"cases[{index}].expected.facts")


def _validate_fact_list(value: list[Any], *, field_name: str) -> None:
    for index, fact in enumerate(value):
        if not isinstance(fact, dict):
            raise ValueError(f"{field_name}[{index}] must be an object.")
        for key in ("fact_key", "subject"):
            _required_string(fact.get(key), field_name=f"{field_name}[{index}].{key}")
        if "value" not in fact:
            raise ValueError(f"{field_name}[{index}].value is required.")


def _sorted_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "fact_key": str(fact["fact_key"]),
                "subject": str(fact["subject"]),
                "value": fact["value"],
            }
            for fact in facts
        ],
        key=lambda fact: (fact["fact_key"], fact["subject"], json.dumps(fact["value"], ensure_ascii=False)),
    )


def _required_string(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return normalized


def _write_junit_report(*, report: dict[str, Any], output_path: Path) -> None:
    suite = Element(
        "testsuite",
        {
            "name": "agent-fact-extraction-eval",
            "tests": str(report["total"]),
            "failures": str(report["failed"]),
        },
    )
    for result in report["results"]:
        testcase = SubElement(
            suite,
            "testcase",
            {
                "classname": "user-fact-extraction",
                "name": str(result["id"]),
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
            failure_node.text = json.dumps(failure, ensure_ascii=False, sort_keys=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(tostring(suite, encoding="utf-8", xml_declaration=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run observed deterministic user fact extraction evals.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", dest="case_ids", default=[])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--junit-output", type=Path, default=None)
    args = parser.parse_args()

    report = asyncio.run(
        run_fact_extraction_eval(
            cases_path=args.cases,
            case_ids=set(args.case_ids) or None,
            output_path=args.output,
            junit_output_path=args.junit_output,
        )
    )
    if args.output is None:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
