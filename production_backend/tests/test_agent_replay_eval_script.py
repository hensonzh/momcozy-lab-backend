import json
from pathlib import Path

from production_backend.scripts.run_agent_replay_eval import run_replay_eval


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_run_agent_replay_eval_writes_passing_report(tmp_path: Path) -> None:
    replay_path = tmp_path / "replay.json"
    output_path = tmp_path / "report.json"
    replay_path.write_text(
        json.dumps(
            {
                "messages": [{"role": "assistant", "content": {"text": "Please confirm this memory."}}],
                "events": [{"type": "action.confirmation_required"}],
                "tool_calls": [{"tool_name": "memory.create.propose", "status": "completed"}],
                "actions": [{"action_type": "agent.memory.create", "status": "confirmation_required"}],
                "safety_events": [{"decision": "allow"}],
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
                "tool_calls": [],
                "actions": [],
                "safety_events": [{"decision": "allow"}],
            }
        )
    )

    report = run_replay_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        replay_path=replay_path,
        suite="memory_preference_capture",
    )

    assert report["passed"] is False
    assert [failure["category"] for failure in report["failures"]] == ["missing_tool", "missing_confirmation"]
