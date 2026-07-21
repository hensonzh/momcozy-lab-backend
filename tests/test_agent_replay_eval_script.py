import json
from pathlib import Path

from scripts.run_agent_replay_eval import run_replay_eval


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_run_agent_replay_eval_writes_passing_report(tmp_path: Path) -> None:
    replay_path = tmp_path / "replay.json"
    output_path = tmp_path / "report.json"
    replay_path.write_text(
        json.dumps(
            {
                "messages": [{"role": "assistant", "content": {"text": "I will keep reminders concise."}}],
                "events": [],
                "tool_calls": [],
                "actions": [],
            }
        )
    )

    report = run_replay_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        replay_path=replay_path,
        suite="memory_preference_capture",
        output_path=output_path,
    )

    assert report["passed"] is True
    assert report["failures"] == []
    assert json.loads(output_path.read_text()) == report


def test_run_agent_replay_eval_reports_blocking_failures(tmp_path: Path) -> None:
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "messages": [{"role": "assistant", "content": {"text": "Saved."}}],
                "events": [],
                "tool_calls": [{"tool_name": "profile.update", "status": "completed"}],
                "actions": [],
            }
        )
    )

    report = run_replay_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        replay_path=replay_path,
        suite="memory_preference_capture",
    )

    assert report["passed"] is False
    assert [failure["category"] for failure in report["failures"]] == ["forbidden_tool"]
