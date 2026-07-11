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


def test_agent_eval_seed_assertion_engine_passes_live_run_without_memory_side_effect() -> None:
    case = _case("memory_preference_capture")
    trace = AgentEvalTrace(
        safety_decision="allow",
        final_text="好的，接下来我会尽量用简短的方式提醒你。",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_profile_write_for_memory_request() -> None:
    case = _case("memory_sensitive_rejection")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "profile_update", "status": "completed"}],
        safety_decision="allow",
        final_text="Saved to profile.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_tool"
    assert result.failures[0].assertion == "tool.forbidden"
    assert result.failures[0].observed == "profile_update"


def test_agent_eval_seed_assertion_engine_accepts_synchronous_diary_create() -> None:
    case = _case("pregnancy_diary_entry")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "pregnancy_diary.entry.create", "status": "completed"}],
        safety_decision="allow",
        final_text="Saved.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_enforces_diary_conflict_update_order_and_append_mode() -> None:
    case = {
        **_case("pregnancy_diary_entry"),
        "expected_tool_calls": [
            {"contract": "pregnancy_diary.entry.create"},
            {
                "contract": "pregnancy_diary.entry.update",
                "args_subset": {"content_mode": "append"},
            },
        ],
    }
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "pregnancy_diary.entry.update",
                "status": "completed",
                "safe_args": {"content_mode": "replace"},
            },
            {"tool_name": "pregnancy_diary.entry.create", "status": "completed"},
        ],
        safety_decision="allow",
        final_text="Saved.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert {failure.category for failure in result.failures} == {
        "tool_argument_mismatch",
        "tool_order_mismatch",
    }


def test_agent_eval_seed_assertion_engine_requires_health_flow_to_continue_after_diary_write() -> None:
    case = {
        **_case("pregnancy_diary_health_mixed"),
        "expected_behavior": {
            **_case("pregnancy_diary_health_mixed")["expected_behavior"],
            "requires_final_response_after_tools": True,
        },
    }
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "pregnancy_diary.entry.create", "status": "completed"}],
        safety_decision="allow",
        final_text="",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[-1].category == "missing_final_response"


@pytest.mark.parametrize(
    "suite",
    ["pregnancy_diary_opt_out", "pregnancy_diary_negative", "pregnancy_diary_plan_intent"],
)
def test_agent_eval_seed_assertion_engine_rejects_diary_write_for_negative_cases(suite: str) -> None:
    case = _case(suite)
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "pregnancy_diary.entry.create", "status": "completed"}],
        safety_decision="allow",
        final_text="Saved.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_tool"
    assert result.failures[0].assertion == "tool.forbidden"
    assert result.failures[0].observed == "pregnancy_diary.entry.create"


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
        tool_calls=[{"tool_name": "hospital_bag_cart_update", "status": "completed"}],
        events=[{"type": "action.confirmation_required"}],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "confirmation_required"}],
        safety_decision="escalate",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_side_effect"
    assert result.failures[0].assertion == "side_effect.none"
    assert result.failures[0].observed == "hospital_bag_cart_update"


def test_agent_eval_seed_assertion_engine_passes_hospital_bag_cart_trace() -> None:
    case = _case("hospital_bag_cart_update")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "hospital_bag_cart_update", "status": "completed"}],
        events=[{"type": "action.queued"}],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "confirmed"}],
        safety_decision="allow",
        final_text="I queued that cart update.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_treats_cozymate_as_wrapper_for_scene_skill_expectation() -> None:
    case = _case("milk_daily_summary")
    trace = AgentEvalTrace(
        tool_calls=[
            {"tool_name": "records.milk_status.read", "status": "completed"},
            {"tool_name": "records.milk_summary.read", "status": "completed"},
        ],
        safety_decision="allow",
        service_skill_id="cozymate_service_agent",
        final_text="Here is your milk summary.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_wrong_scene_skill_when_trace_has_route() -> None:
    case = _case("milk_daily_summary")
    trace = AgentEvalTrace(
        tool_calls=[
            {"tool_name": "records.milk_status.read", "status": "completed"},
            {"tool_name": "records.milk_summary.read", "status": "completed"},
        ],
        safety_decision="allow",
        service_skill_id="birth-prep",
        final_text="Here is your milk summary.",
    )

    result = AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "routing_mismatch"
    assert result.failures[0].expected == "milk-management"
    assert result.failures[0].observed == "birth-prep"


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
            {"role": "assistant", "content": {"text": "I will keep reminders concise."}},
        ],
        "events": [],
        "tool_calls": [],
        "actions": [],
        "safety_events": [{"decision": "allow"}],
    }

    trace = agent_eval_trace_from_replay_bundle(replay_bundle)
    result = AgentEvalReplayAssertionRunner().evaluate_bundle(case=case, replay_bundle=replay_bundle)

    assert trace.final_text == "I will keep reminders concise."
    assert trace.safety_decision == "allow"
    assert result.passed is True


def _case(suite: str) -> dict:
    return next(case for case in load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED) if case["suite"] == suite)
