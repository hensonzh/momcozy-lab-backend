from pathlib import Path

from production_backend.app.modules.agent_runtime.evals import (
    AgentEvalReplayAssertionRunner,
    AgentEvalSeedAssertionEngine,
    AgentEvalTrace,
    agent_eval_trace_from_replay_bundle,
    load_product_agent_eval_seed_cases,
)


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_agent_eval_seed_assertion_engine_passes_memory_preference_trace() -> None:
    case = _case("memory_preference_capture")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "memory.create.propose", "status": "completed"}],
        events=[{"type": "action.confirmation_required"}],
        actions=[{"action_type": "agent.memory.create", "status": "confirmation_required"}],
        safety_decision="allow",
        final_text="I can remember that after you confirm.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_missing_tool_and_confirmation() -> None:
    case = _case("memory_preference_capture")
    trace = AgentEvalTrace(
        tool_calls=[],
        events=[],
        actions=[],
        safety_decision="allow",
        final_text="Saved.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert [(failure.category, failure.assertion) for failure in result.failures] == [
        ("missing_tool", "tool.required"),
        ("missing_confirmation", "action.confirmation_required"),
    ]


def test_agent_eval_seed_assertion_engine_reports_safety_mismatch() -> None:
    case = _case("emotion_support")
    trace = AgentEvalTrace(safety_decision="allow", final_text="Let's continue with your plan.")

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "safety_mismatch"
    assert result.failures[0].expected == "escalate"
    assert result.failures[0].observed == "allow"


def test_agent_eval_replay_runner_evaluates_replay_bundle_trace() -> None:
    case = _case("memory_preference_capture")
    replay_bundle = {
        "messages": [
            {"role": "user", "content": {"text": "Remember my reminder style."}},
            {"role": "assistant", "content": {"text": "Please confirm this memory."}},
        ],
        "events": [{"type": "action.confirmation_required"}],
        "tool_calls": [{"tool_name": "memory.create.propose", "status": "completed"}],
        "actions": [{"action_type": "agent.memory.create", "status": "confirmation_required"}],
        "safety_events": [{"decision": "allow"}],
    }

    trace = agent_eval_trace_from_replay_bundle(replay_bundle)
    result = AgentEvalReplayAssertionRunner().evaluate_bundle(case=case, replay_bundle=replay_bundle)

    assert trace.final_text == "Please confirm this memory."
    assert trace.safety_decision == "allow"
    assert result.passed is True


def _case(suite: str) -> dict:
    return next(case for case in load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED) if case["suite"] == suite)
