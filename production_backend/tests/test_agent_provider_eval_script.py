from pathlib import Path

from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response, scripted_tool_invocation
from production_backend.scripts.run_agent_provider_eval import run_provider_eval


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_run_agent_provider_eval_skips_without_credentials(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    output_path = tmp_path / "provider-eval.json"

    report = run_provider_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="milk_daily_summary",
        output_path=output_path,
        cost_budget_usd="3.50",
        allow_skip_without_credentials=True,
    )

    assert report["total"] == 1
    assert report["provider"] == "openai"
    assert report["model"] == "gpt-5.5"
    assert report["failed"] == 0
    assert report["skipped"] == 1
    assert report["results"][0]["skip_reason"] == "missing_provider_credentials"
    assert report["budget"]["cost_budget_usd"] == "3.50"
    assert "missing_provider_credentials" in output_path.read_text()


def test_run_agent_provider_eval_skips_without_minimax_credentials(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    output_path = tmp_path / "provider-eval-minimax.json"

    report = run_provider_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="milk_daily_summary",
        output_path=output_path,
        allow_skip_without_credentials=True,
    )

    assert report["provider"] == "minimax"
    assert report["model"] == "MiniMax-M3"
    assert report["skipped"] == 1
    assert report["results"][0]["skip_reason"] == "missing_provider_credentials"
    assert "minimax" in output_path.read_text()


def test_run_agent_provider_eval_uses_sdk_runner_and_seed_assertions() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="You completed 3 of 5 milk tasks today.",
                tool_invocations=(
                    scripted_tool_invocation("records.milk_status.read", {"days": 1}),
                    scripted_tool_invocation("records.milk_summary.read", {"days": 1}),
                ),
                expected_available_tools=("records.milk_status.read", "records.milk_summary.read"),
            )
        ]
    )

    report = run_provider_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="milk_daily_summary",
        sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
    )

    assert report["failed"] == 0
    assert report["provider"] == "openai"
    assert report["model"] == "gpt-5.5"
    assert report["passed"] == 1
    assert report["results"][0]["status"] == "passed"
    assert report["results"][0]["service_skill_id"] == "milk-management"
    assert report["results"][0]["routing_source"] == "local_hint"
    assert [tool_call["tool_name"] for tool_call in report["results"][0]["observed_tool_calls"]] == [
        "records.milk_status.read",
        "records.milk_summary.read",
    ]


def test_run_agent_provider_eval_uses_budget_metadata_with_sdk_runner() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="Recovery check-in complete.",
                tool_invocations=(
                    scripted_tool_invocation("profile.read", {}),
                    scripted_tool_invocation("plans.current.read", {}),
                    scripted_tool_invocation("diary.recent.read", {}),
                ),
                expected_available_tools=("profile.read", "plans.current.read", "diary.recent.read"),
            )
        ]
    )

    report = run_provider_eval(
        cases_path=PRODUCT_AGENT_EVAL_SEED,
        suite="postpartum_recovery_checkin",
        max_cases=1,
        cost_budget_usd="1.25",
        sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
    )

    assert report["budget"] == {"max_cases": 1, "cost_budget_usd": "1.25"}
    assert report["provider"] == "openai"
    assert report["total"] == 1
