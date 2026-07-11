from pathlib import Path

import pytest

from production_backend.app.modules.agent_runtime.evals.service import (
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
        assert behavior["service_skill_id"] in {
            "cozymate_service_agent",
            "birth-prep",
            "milk-management",
            "health-consultation",
            "device-guidance",
            "emotion-support",
        }
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
    daily_contracts = {tool_call["contract"] for tool_call in milk_cases["milk_daily_summary"]["expected_tool_calls"]}
    assert "records.milk_status.read" in daily_contracts


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

    assert "load_service_skill" in plan_contracts
    assert "pregnancy.plan.propose" in plan_contracts
    assert "load_service_skill" in task_contracts
    assert "plans.task_complete.propose" in task_contracts
    assert "diary.entry_upsert.propose" in diary_contracts
    assert "pregnancy_plan_proposal" not in plan_contracts
    assert "plan_task_update_proposal" not in task_contracts
    assert "diary_entry_upsert_proposal" not in diary_contracts


def test_product_agent_eval_seed_uses_current_pregnancy_artifact_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    birth_prep_contracts = {tool_call["contract"] for tool_call in by_suite["birth_prep"]["expected_tool_calls"]}
    communication_contracts = {tool_call["contract"] for tool_call in by_suite["labor_communication"]["expected_tool_calls"]}

    assert "load_service_skill" in birth_prep_contracts
    assert "hospital_bag_card_create" in birth_prep_contracts
    assert "labor_communication_card_create" in communication_contracts
    assert "birth_prep_intake" not in birth_prep_contracts
    assert "labor_communication_draft" not in communication_contracts


def test_product_agent_eval_seed_uses_current_hospital_bag_action_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    cart_contracts = {tool_call["contract"] for tool_call in by_suite["hospital_bag_cart_update"]["expected_tool_calls"]}

    assert "hospital_bag_cart_update" in cart_contracts
    assert by_suite["hospital_bag_cart_update"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["hospital_bag_cart_update"]["expected_behavior"]["service_skill_id"] == "birth-prep"
    assert "hospital_bag_cart_update_proposal" not in cart_contracts


def test_product_agent_eval_seed_uses_current_support_action_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    support_contracts = {tool_call["contract"] for tool_call in by_suite["device_support_handoff"]["expected_tool_calls"]}
    ibclc_contracts = {tool_call["contract"] for tool_call in by_suite["ibclc_consult"]["expected_tool_calls"]}

    assert "support.ticket.propose" in support_contracts
    assert "support.ticket.propose" in ibclc_contracts
    assert "support_ticket_proposal" not in support_contracts
    assert "ibclc_consult_proposal" not in ibclc_contracts


def test_product_agent_eval_seed_does_not_reference_missing_device_tool_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    clarify_contracts = {tool_call["contract"] for tool_call in by_suite["device_guidance"]["expected_tool_calls"]}
    known_device_contracts = {tool_call["contract"] for tool_call in by_suite["device_known_guidance"]["expected_tool_calls"]}

    assert "device_reference_lookup" not in clarify_contracts | known_device_contracts
    assert "devices.pump_status.read" not in clarify_contracts
    assert by_suite["device_guidance"]["expected_behavior"]["must_clarify"] == ["device_model", "first_use_context"]
    assert "devices.pump_status.read" in known_device_contracts
    assert "devices.guidance_assets.read" in known_device_contracts
    assert by_suite["device_known_guidance"]["expected_behavior"]["requires_confirmation_before_write"] is False


def test_product_agent_eval_seed_keeps_memory_writes_off_live_run_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    memory_contracts = {tool_call["contract"] for tool_call in by_suite["memory_preference_capture"]["expected_tool_calls"]}
    forbidden_preference_contracts = {
        tool_call["contract"] for tool_call in by_suite["memory_preference_capture"]["forbidden_tool_calls"]
    }
    forbidden_sensitive_contracts = {
        tool_call["contract"] for tool_call in by_suite["memory_sensitive_rejection"]["forbidden_tool_calls"]
    }

    assert memory_contracts == set()
    assert "profile_update" in forbidden_preference_contracts
    assert by_suite["memory_preference_capture"]["expected_behavior"]["route"] == "async_memory_consolidation"
    assert by_suite["memory_preference_capture"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["memory_sensitive_rejection"]["expected_tool_calls"] == []
    assert "profile_update" in forbidden_sensitive_contracts
    assert by_suite["memory_sensitive_rejection"]["expected_behavior"]["requires_confirmation_before_write"] is False


def test_product_agent_eval_seed_covers_postpartum_recovery_service_skill() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    checkin_contracts = {tool_call["contract"] for tool_call in by_suite["postpartum_recovery_checkin"]["expected_tool_calls"]}
    task_contracts = {tool_call["contract"] for tool_call in by_suite["postpartum_recovery_task"]["expected_tool_calls"]}

    assert by_suite["postpartum_recovery_checkin"]["expected_behavior"]["service_skill_id"] == "health-consultation"
    assert by_suite["postpartum_recovery_checkin"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert {"profile.read", "plans.current.read", "diary.recent.read"} <= checkin_contracts
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["service_skill_id"] == "health-consultation"
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["requires_confirmation_before_write"] is True
    assert "plans.task_create.propose" in task_contracts


def test_product_agent_eval_seed_covers_critical_health_and_emotion_regressions() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    for suite in ("health_consultation", "infant_health_red_flag", "emotion_support", "emotion_harm_baby"):
        assert by_suite[suite]["expected_safety_decision"] == "escalate"
        assert by_suite[suite]["expected_tool_calls"] == []
        assert by_suite[suite]["expected_behavior"]["requires_confirmation_before_write"] is False


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
