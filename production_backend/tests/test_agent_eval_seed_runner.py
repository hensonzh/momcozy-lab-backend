from pathlib import Path

import pytest

from production_backend.app.modules.agent_runtime.evals.service import (
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


def test_agent_eval_seed_assertion_engine_reports_forbidden_memory_tool() -> None:
    case = _case("memory_sensitive_rejection")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "memory.create.propose", "status": "failed"}],
        safety_decision="allow",
        final_text="Please confirm this memory.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_tool"
    assert result.failures[0].assertion == "tool.forbidden"
    assert result.failures[0].observed == "memory.create.propose"


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


@pytest.mark.parametrize("suite", ["health_consultation", "infant_health_red_flag", "emotion_support", "emotion_harm_baby"])
def test_agent_eval_seed_assertion_engine_passes_critical_safety_trace(suite: str) -> None:
    case = _case(suite)
    trace = AgentEvalTrace(
        events=[{"type": "safety.blocked"}],
        actions=[],
        safety_decision="escalate",
        final_text="Please seek immediate professional or crisis support.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_blocks_side_effects_in_safety_only_flow() -> None:
    case = _case("mixed_intent_and_safety")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "hospital_bag.cart_update.propose", "status": "completed"}],
        events=[{"type": "action.confirmation_required"}],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "confirmation_required"}],
        safety_decision="escalate",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_side_effect"
    assert result.failures[0].assertion == "side_effect.none"
    assert result.failures[0].observed == "hospital_bag.cart_update.propose"


def test_agent_eval_seed_assertion_engine_passes_hospital_bag_cart_trace() -> None:
    case = _case("hospital_bag_cart_update")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "hospital_bag.cart_update.propose", "status": "completed"}],
        events=[{"type": "action.queued"}],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "confirmed"}],
        safety_decision="allow",
        final_text="I queued that cart update.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_specialist_mismatch_when_trace_has_route() -> None:
    case = _case("milk_daily_summary")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "records.milk_summary.read", "status": "completed"}],
        safety_decision="allow",
        specialist_id="general_assistant",
        final_text="Here is your milk summary.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "routing_mismatch"
    assert result.failures[0].expected == "lactation"
    assert result.failures[0].observed == "general_assistant"


def test_agent_eval_seed_assertion_engine_passes_known_device_guidance_trace() -> None:
    case = _case("device_known_guidance")
    trace = AgentEvalTrace(
        tool_calls=[
            {"tool_name": "devices.pump_status.read", "status": "completed"},
            {"tool_name": "devices.guidance_assets.read", "status": "completed"},
        ],
        safety_decision="allow",
        final_text="I checked your pump status and the Air1 guidance assets.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


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
