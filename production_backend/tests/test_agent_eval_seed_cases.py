from pathlib import Path

import pytest

from production_backend.app.modules.agent_runtime.evals import (
    REQUIRED_PRODUCT_AGENT_EVAL_SUITES,
    load_product_agent_eval_seed_cases,
    validate_product_agent_eval_seed_payload,
)


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_product_agent_eval_seed_covers_required_suites() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    suites = {case["suite"] for case in cases}

    assert set(REQUIRED_PRODUCT_AGENT_EVAL_SUITES) <= suites
    assert len(cases) >= len(REQUIRED_PRODUCT_AGENT_EVAL_SUITES)


def test_product_agent_eval_seed_cases_have_action_and_safety_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    for case in cases:
        behavior = case["expected_behavior"]
        assert behavior["intent"] == case["suite"]
        assert isinstance(behavior["route"], str) and behavior["route"]
        assert isinstance(behavior["requires_confirmation_before_write"], bool)
        assert case["expected_safety_decision"] in {"allow", "escalate", "block"}
        assert "messages" in case["input"]
        assert isinstance(case["expected_tool_calls"], list)


def test_product_agent_eval_seed_uses_current_milk_summary_tool_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    milk_cases = {case["suite"]: case for case in cases if case["suite"] in {"milk_daily_summary", "milk_trend_analysis"}}

    assert set(milk_cases) == {"milk_daily_summary", "milk_trend_analysis"}
    for case in milk_cases.values():
        contracts = {tool_call["contract"] for tool_call in case["expected_tool_calls"]}
        assert "records.milk_summary.read" in contracts
        assert "milk_summary_read" not in contracts
        assert "milk_records_read" not in contracts


def test_product_agent_eval_seed_uses_current_milk_action_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    schedule_contracts = {tool_call["contract"] for tool_call in by_suite["milk_schedule_management"]["expected_tool_calls"]}
    plan_contracts = {tool_call["contract"] for tool_call in by_suite["milk_plan_creation"]["expected_tool_calls"]}

    assert "notifications.milk_reminder.propose" in schedule_contracts
    assert "plan_task_update_proposal" not in schedule_contracts
    assert "records.milk_summary.read" in plan_contracts
    assert "plans.milk_plan.propose" in plan_contracts
    assert "milk_plan_proposal" not in plan_contracts


def test_product_agent_eval_seed_uses_current_pregnancy_action_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    plan_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_creation"]["expected_tool_calls"]}
    task_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_task_completion"]["expected_tool_calls"]}
    diary_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_diary_entry"]["expected_tool_calls"]}

    assert "plans.current.read" in plan_contracts
    assert "pregnancy.plan_create.propose" in plan_contracts
    assert "plans.current.read" in task_contracts
    assert "plans.task_complete.propose" in task_contracts
    assert "diary.entry_upsert.propose" in diary_contracts
    assert "pregnancy_plan_proposal" not in plan_contracts
    assert "plan_task_update_proposal" not in task_contracts
    assert "diary_entry_upsert_proposal" not in diary_contracts


def test_product_agent_eval_seed_uses_current_support_action_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    support_contracts = {tool_call["contract"] for tool_call in by_suite["device_support_handoff"]["expected_tool_calls"]}

    assert "support.ticket.propose" in support_contracts
    assert "support_ticket_proposal" not in support_contracts


def test_product_agent_eval_seed_uses_current_memory_action_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    memory_contracts = {tool_call["contract"] for tool_call in by_suite["memory_preference_capture"]["expected_tool_calls"]}

    assert "memory.create.propose" in memory_contracts
    assert "agent.memory.create" not in memory_contracts
    assert "memory_write" not in memory_contracts


def test_product_agent_eval_seed_loader_rejects_missing_required_suite() -> None:
    payload = {
        "schema_version": "agent_eval_seed.v1",
        "cases": [
            {
                "suite": "birth_prep",
                "name": "incomplete seed",
                "domain": "birth_prep",
                "input": {"messages": []},
                "expected_behavior": {"intent": "birth_prep", "route": "structured_service", "requires_confirmation_before_write": True},
                "expected_tool_calls": [],
                "expected_safety_decision": "allow",
            }
        ],
    }

    with pytest.raises(ValueError, match="missing required suites"):
        validate_product_agent_eval_seed_payload(payload)
