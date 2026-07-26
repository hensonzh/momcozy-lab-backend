from pathlib import Path

import pytest

from app.agents.cozymate.evals import (
    create_cozymate_eval_assertion_engine,
    load_product_agent_eval_seed_cases,
)
from app.agent_runtime.evals.service import (
    AgentEvalReplayAssertionRunner,
    AgentEvalTrace,
    agent_eval_trace_from_replay_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_agent_eval_seed_assertion_engine_passes_live_run_without_memory_side_effect() -> None:
    case = _case("memory_preference_capture")
    trace = AgentEvalTrace(
        final_text="好的，接下来我会尽量用简短的方式提醒你。",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_profile_update_for_memory_request() -> None:
    case = _case("memory_sensitive_rejection")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "profile_update", "status": "completed"}],
        final_text="Saved to profile.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_tool"
    assert result.failures[0].assertion == "tool.forbidden"
    assert result.failures[0].observed == "profile_update"


def test_agent_eval_seed_assertion_engine_scopes_forbidden_plan_calls_by_type() -> None:
    case = {
        "expected_tool_calls": [],
        "forbidden_tool_calls": [
            {
                "contract": "plan_mutate",
                "args_subset": {"operation": "create", "plan_type": "pregnancy"},
            }
        ],
        "expected_behavior": {},
    }

    allowed = create_cozymate_eval_assertion_engine().evaluate(
        case=case,
        trace=AgentEvalTrace(
            tool_calls=[
                {
                    "tool_name": "plan_mutate",
                    "status": "completed",
                    "safe_args": {"operation": "create", "plan_type": "milk_management"},
                }
            ],
        ),
    )
    forbidden = create_cozymate_eval_assertion_engine().evaluate(
        case=case,
        trace=AgentEvalTrace(
            tool_calls=[
                {
                    "tool_name": "plan_mutate",
                    "status": "completed",
                    "safe_args": {"operation": "create", "plan_type": "pregnancy"},
                }
            ],
        ),
    )

    assert allowed.passed is True
    assert forbidden.passed is False
    assert forbidden.failures[0].category == "forbidden_tool"
    assert forbidden.failures[0].observed == "plan_mutate[operation=create]"


def test_agent_eval_seed_assertion_engine_accepts_synchronous_diary_write() -> None:
    case = _case("diary_pregnancy_entry")
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "diary_mutate",
                "status": "completed",
                "safe_args": {"operation": "create"},
            }
        ],
        final_text="Saved.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_enforces_diary_write_then_complete_update_order() -> None:
    case = {
        **_case("diary_pregnancy_entry"),
        "expected_tool_calls": [
            {
                "contract": "diary_mutate",
                "args_subset": {"operation": "create"},
            },
            {
                "contract": "diary_mutate",
                "args_subset": {"operation": "update"},
            },
        ],
    }
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "diary_mutate",
                "status": "completed",
                "safe_args": {"operation": "update"},
            },
            {
                "tool_name": "diary_mutate",
                "status": "completed",
                "safe_args": {"operation": "create"},
            },
        ],
        final_text="Saved.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert {failure.category for failure in result.failures} == {
        "tool_argument_mismatch",
        "tool_order_mismatch",
    }


def test_agent_eval_seed_assertion_engine_requires_health_flow_to_continue_after_diary_write() -> None:
    case = {
        **_case("diary_pregnancy_health_mixed"),
        "expected_behavior": {
            **_case("diary_pregnancy_health_mixed")["expected_behavior"],
            "requires_final_response_after_tools": True,
        },
    }
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "diary_mutate",
                "status": "completed",
                "safe_args": {"operation": "create"},
            }
        ],
        final_text="",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[-1].category == "missing_final_response"


@pytest.mark.parametrize(
    "suite",
    ["diary_pregnancy_opt_out", "diary_pregnancy_negative", "diary_pregnancy_plan_intent"],
)
def test_agent_eval_seed_assertion_engine_rejects_diary_write_for_negative_cases(suite: str) -> None:
    case = _case(suite)
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "diary_mutate",
                "status": "completed",
                "safe_args": {"operation": "create"},
            }
        ],
        final_text="Saved.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_tool"
    assert result.failures[0].assertion == "tool.forbidden"
    assert result.failures[0].observed == "diary_mutate"


@pytest.mark.parametrize(
    "suite",
    ["maternal_health_red_flag", "infant_health_red_flag", "self_harm_safety", "baby_harm_safety"],
)
def test_agent_eval_seed_assertion_engine_passes_critical_response_trace(suite: str) -> None:
    case = _case(suite)
    trace = AgentEvalTrace(
        actions=[],
        final_text="Please seek immediate professional or crisis support.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_blocks_side_effects_in_safety_only_flow() -> None:
    case = _case("mixed_intent_and_safety")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "hospital_bag_cart_mutate", "status": "completed"}],
        events=[{"type": "action.confirmation_required"}],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "confirmation_required"}],
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "forbidden_side_effect"
    assert result.failures[0].assertion == "side_effect.none"
    assert result.failures[0].observed == "hospital_bag_cart_mutate"


def test_agent_eval_seed_assertion_engine_passes_hospital_bag_cart_trace() -> None:
    case = _case("hospital_bag_cart_mutate")
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "hospital_bag_cart_mutate",
                "status": "completed",
                "safe_args": {"operation": "reset_cart"},
            }
        ],
        events=[
            {"type": "action.applied", "payload": {"action_type": "hospital_bag.cart.update"}},
            {"type": "hospital_bag.cart.changed", "payload": {"operation": "updated"}},
        ],
        actions=[{"action_type": "hospital_bag.cart.update", "status": "applied"}],
        final_text="I updated that cart.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_treats_cozymate_as_wrapper_for_scene_skill_expectation() -> None:
    case = _case("milk_daily_summary")
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "schedule_timeline_read",
                "status": "completed",
                "args": {"domains": ["lactation"]},
            }
        ],
        service_skill_id="cozymate_service_agent",
        final_text="Here is your milk summary.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_reports_wrong_scene_skill_when_trace_has_route() -> None:
    case = _case("milk_daily_summary")
    trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "schedule_timeline_read",
                "status": "completed",
                "args": {"domains": ["lactation"]},
            }
        ],
        service_skill_id="birth-prep",
        final_text="Here is your milk summary.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is False
    assert result.failures[0].category == "routing_mismatch"
    assert result.failures[0].expected == "milk-management"
    assert result.failures[0].observed == "birth-prep"


def test_agent_eval_seed_assertion_engine_passes_known_device_guidance_trace() -> None:
    case = _case("device_known_guidance")
    trace = AgentEvalTrace(
        tool_calls=[{"tool_name": "devices_guidance_manage", "status": "completed"}],
        final_text="I checked the official Air1 guidance assets.",
    )

    result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=trace)

    assert result.passed is True
    assert result.failures == []


def test_agent_eval_seed_assertion_engine_forbids_cart_update_during_pump_recommendation() -> None:
    case = _case("device_pump_recommendation")
    read_only_trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "pump_models_read",
                "status": "completed",
                "safe_args": {},
            }
        ],
        final_text="M9 best matches work, quiet operation, app control, and your budget.",
    )
    cart_mutation_trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "pump_models_read",
                "status": "completed",
                "safe_args": {},
            },
            {
                "tool_name": "hospital_bag_cart_mutate",
                "status": "completed",
                "safe_args": {
                    "operation": "replace_pump_model",
                    "product_sku_id": "pump-m9",
                },
            },
        ],
        final_text="M9 fits, and I added it to your cart.",
    )

    read_only_result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=read_only_trace)
    cart_mutation_result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=cart_mutation_trace)

    assert read_only_result.passed is True
    assert read_only_result.failures == []
    assert cart_mutation_result.passed is False
    assert cart_mutation_result.failures[0].category == "forbidden_tool"
    assert cart_mutation_result.failures[0].observed == "hospital_bag_cart_mutate"


def test_agent_eval_seed_assertion_engine_forbids_only_matching_device_guidance_operation() -> None:
    case = {
        **_case("device_unboxing_incomplete_step"),
        "forbidden_tool_calls": [
            {
                "contract": "devices_guidance_manage",
                "args_subset": {"operation": "complete_current"},
            }
        ],
    }
    read_trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "devices_guidance_manage",
                "status": "completed",
                "safe_args": {
                    "model": "Air1",
                    "operation": "read",
                    "step": "guide.parts",
                },
            }
        ],
        final_text="Let us stay on this step and find the missing cable.",
    )
    advance_trace = AgentEvalTrace(
        tool_calls=[
            {
                "tool_name": "devices_guidance_manage",
                "status": "completed",
                "safe_args": {
                    "model": "Air1",
                    "operation": "complete_current",
                },
            }
        ],
        final_text="Here is the next step.",
    )

    read_result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=read_trace)
    advance_result = create_cozymate_eval_assertion_engine().evaluate(case=case, trace=advance_trace)

    assert read_result.passed is True
    assert read_result.failures == []
    assert advance_result.passed is False
    assert advance_result.failures[0].category == "forbidden_tool"
    assert advance_result.failures[0].observed == "devices_guidance_manage[operation=complete_current]"


def test_agent_eval_seed_assertion_engine_requires_expected_application_event() -> None:
    case = {
        **_case("memory_preference_capture"),
        "expected_events": [
            {"type": "CUSTOM", "name": "momcozy.web_search.citations"},
        ],
    }

    missing = create_cozymate_eval_assertion_engine().evaluate(
        case=case,
        trace=AgentEvalTrace(final_text="Answer without sources."),
    )
    observed = create_cozymate_eval_assertion_engine().evaluate(
        case=case,
        trace=AgentEvalTrace(
            events=[
                {
                    "type": "CUSTOM",
                    "payload": {"name": "momcozy.web_search.citations"},
                }
            ],
            final_text="Answer with sources.",
        ),
    )

    assert missing.passed is False
    assert missing.failures[0].category == "missing_event"
    assert missing.failures[0].assertion == "event.required"
    assert observed.passed is True


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
    }

    trace = agent_eval_trace_from_replay_bundle(replay_bundle)
    result = AgentEvalReplayAssertionRunner(assertion_engine=create_cozymate_eval_assertion_engine()).evaluate_bundle(case=case, replay_bundle=replay_bundle)

    assert trace.final_text == "I will keep reminders concise."
    assert result.passed is True


def _case(suite: str) -> dict:
    return next(case for case in load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED) if case["suite"] == suite)
