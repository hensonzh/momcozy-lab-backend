import asyncio
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from production_backend.scripts.run_agent_fact_eval import (
    load_fact_eval_cases,
    run_fact_extraction_eval,
)


ROOT = Path(__file__).resolve().parents[2]
FACT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "user_fact_extraction_seed.json"


def test_fact_eval_fixture_defines_required_observed_scenarios() -> None:
    cases = load_fact_eval_cases(FACT_EVAL_SEED)

    assert {case["id"] for case in cases} == {
        "explicit_self_age_is_extracted",
        "explicit_current_week_is_extracted",
        "friend_age_is_rejected",
        "uncertain_fetus_count_is_rejected",
        "restricted_medical_fact_is_rejected",
        "english_husband_age_is_rejected",
        "proper_name_pregnancy_is_rejected",
        "restricted_value_smuggling_is_rejected",
        "latest_self_correction_wins",
    }
    assert all(case["priority"] == "p0" and case["status"] == "active" for case in cases)


def test_fact_eval_runs_real_extractor_and_writes_ci_reports(tmp_path: Path) -> None:
    output_path = tmp_path / "fact-eval.json"
    junit_output_path = tmp_path / "fact-eval.xml"

    report = asyncio.run(
        run_fact_extraction_eval(
            cases_path=FACT_EVAL_SEED,
            output_path=output_path,
            junit_output_path=junit_output_path,
        )
    )

    assert report["execution_mode"] == "scripted_backend_observed_extraction"
    assert report["total"] == 9
    assert report["passed"] == 9
    assert report["failed"] == 0
    assert all(result["extractor_requests"] == 1 for result in report["results"])
    assert all(result["scripted_candidate_count"] == 1 for result in report["results"])
    assert all(result["prompt_version"] == "turn-fact-extractor-v2" for result in report["results"])
    assert json.loads(output_path.read_text()) == report
    junit = ElementTree.parse(junit_output_path).getroot()
    assert junit.attrib == {
        "name": "agent-fact-extraction-eval",
        "tests": "9",
        "failures": "0",
    }


def test_fact_eval_reports_observed_extraction_mismatch(tmp_path: Path) -> None:
    payload = json.loads(FACT_EVAL_SEED.read_text())
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["expected"]["facts"][0]["value"] = 36
    cases_path = tmp_path / "wrong-expectation.json"
    cases_path.write_text(json.dumps(payload))

    report = asyncio.run(run_fact_extraction_eval(cases_path=cases_path))

    assert report["failed"] == 1
    failure = report["results"][0]["failures"][0]
    assert failure["category"] == "fact_extraction_mismatch"
    assert failure["assertion"] == "facts.exact"
    assert report["results"][0]["observed_facts"] == [
        {"fact_key": "profile.age", "subject": "self", "value": 35}
    ]


def test_fact_eval_rejects_unknown_schema_version(tmp_path: Path) -> None:
    payload = json.loads(FACT_EVAL_SEED.read_text())
    payload["schema_version"] = "agent_fact_eval.v999"
    cases_path = tmp_path / "invalid.json"
    cases_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="schema_version"):
        load_fact_eval_cases(cases_path)
