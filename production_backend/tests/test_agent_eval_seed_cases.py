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


def test_product_agent_eval_seed_cases_have_action_and_response_contracts() -> None:
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

    assert schedule_contracts == {"plans.milk_schedule.propose"}
    assert "plan_task_update_proposal" not in schedule_contracts
    assert plan_contracts == {
        "records.milk_analysis.intake",
        "records.milk_analysis.evaluate",
        "plans.milk_plan.propose",
    }
    assert "milk_plan_proposal" not in plan_contracts


def test_product_agent_eval_seed_uses_current_pregnancy_action_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    plan_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_creation"]["expected_tool_calls"]}
    intake_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_intake_start"]["expected_tool_calls"]}
    analysis_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_intake_analysis"]["expected_tool_calls"]}
    followup_contracts = {
        tool_call["contract"] for tool_call in by_suite["pregnancy_plan_personalized_followup"]["expected_tool_calls"]
    }
    task_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_task_completion"]["expected_tool_calls"]}
    diary_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_diary_entry"]["expected_tool_calls"]}

    assert "load_service_skill" in plan_contracts
    assert "pregnancy.plan.propose" in plan_contracts
    assert by_suite["pregnancy_plan_creation"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in by_suite["pregnancy_plan_creation"]["expected_behavior"]["must_not"]
    assert intake_contracts == {"load_service_skill", "pregnancy.plan_intake.start"}
    assert analysis_contracts == {"pregnancy.plan_intake.analyze"}
    assert followup_contracts == {"pregnancy.plan_intake.advance"}
    assert by_suite["pregnancy_plan_personalized_followup"]["forbidden_tool_calls"] == [
        {"contract": "pregnancy.plan_intake.analyze"},
        {"contract": "pregnancy.plan.propose"}
    ]
    attachment_guard = by_suite["pregnancy_plan_checkup_attachment_guard"]
    assert attachment_guard["input"]["fixtures"]["current_run_authenticated_attachments"] == []
    assert "mark_uploaded_from_text_only" in attachment_guard["expected_behavior"]["must_not"]
    assert by_suite["pregnancy_plan_intake_start"]["forbidden_tool_calls"] == [{"contract": "pregnancy.plan.propose"}]
    assert by_suite["pregnancy_plan_intake_analysis"]["forbidden_tool_calls"] == [{"contract": "pregnancy.plan.propose"}]
    assert "load_service_skill" in task_contracts
    assert "plans.task_complete.propose" in task_contracts
    assert diary_contracts == {"pregnancy_diary.manage"}
    assert by_suite["pregnancy_diary_entry"]["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"
    assert by_suite["pregnancy_diary_entry"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "pregnancy_plan_proposal" not in plan_contracts
    assert "plan_task_update_proposal" not in task_contracts
    assert "diary_entry_upsert_proposal" not in diary_contracts

    diary_delete = by_suite["pregnancy_diary_delete_exact"]
    assert diary_delete["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert diary_delete["input"]["fixtures"]["explicit_delete_intent"] is True
    assert "oral_confirmation_complete" not in diary_delete["input"]["fixtures"]
    assert "redundant_confirmation_question" in diary_delete["expected_behavior"]["must_not"]
    assert diary_delete["expected_tool_calls"] == [
        {
            "contract": "pregnancy_diary.manage",
            "timing": "on_explicit_intent_and_exact_target",
            "args_subset": {"action": "delete", "entry_date": "2026-07-04", "confirmed": True},
        }
    ]
    assert by_suite["pregnancy_diary_delete_ambiguous"]["expected_tool_calls"] == []
    assert by_suite["pregnancy_diary_delete_ambiguous"]["forbidden_tool_calls"] == [
        {"contract": "pregnancy_diary.manage"}
    ]

    plan_delete = by_suite["pregnancy_plan_delete_exact"]
    assert plan_delete["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert plan_delete["input"]["fixtures"]["explicit_delete_intent"] is True
    assert "oral_confirmation_complete" not in plan_delete["input"]["fixtures"]
    assert plan_delete["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "redundant_confirmation_question" in plan_delete["expected_behavior"]["must_not"]
    assert plan_delete["expected_tool_calls"] == [
        {
            "contract": "plans.plan_delete.propose",
            "timing": "on_explicit_intent_and_exact_target",
            "args_subset": {"plan_id": "11111111-1111-4111-8111-111111111111"},
        }
    ]
    assert by_suite["pregnancy_plan_delete_ambiguous"]["expected_tool_calls"] == []
    assert by_suite["pregnancy_plan_delete_ambiguous"]["forbidden_tool_calls"] == [
        {"contract": "plans.plan_delete.propose"}
    ]

    task_exact = by_suite["pregnancy_task_completion"]
    assert task_exact["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert task_exact["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in task_exact["expected_behavior"]["must_not"]
    assert by_suite["pregnancy_task_ambiguous"]["expected_tool_calls"] == []
    assert {item["contract"] for item in by_suite["pregnancy_task_ambiguous"]["forbidden_tool_calls"]} == {
        "plans.task_complete.propose",
        "plans.task_update.propose",
        "plans.task_delete.propose",
    }

    record_exact = by_suite["feeding_record_delete_exact"]
    assert record_exact["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert record_exact["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["feeding_record_delete_ambiguous"]["expected_tool_calls"] == []


def test_product_agent_eval_seed_covers_implicit_opt_out_negative_and_health_mixed_diary_parity() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}
    diary_manage = "pregnancy_diary.manage"

    implicit = by_suite["pregnancy_diary_implicit_entry"]
    assert {call["contract"] for call in implicit["expected_tool_calls"]} == {diary_manage}
    assert implicit["expected_tool_calls"][0]["args_subset"] == {"action": "write"}
    assert "require_explicit_save_phrase" in implicit["expected_behavior"]["must_not"]
    assert implicit["expected_behavior"]["requires_confirmation_before_write"] is False

    for suite in ("pregnancy_diary_entry", "pregnancy_diary_implicit_entry", "pregnancy_diary_existing_entry"):
        assert {call["contract"] for call in by_suite[suite]["forbidden_tool_calls"]} == {"load_service_skill"}

    existing = by_suite["pregnancy_diary_existing_entry"]
    assert [call["contract"] for call in existing["expected_tool_calls"]] == [diary_manage, diary_manage]
    assert [call["args_subset"] for call in existing["expected_tool_calls"]] == [
        {"action": "write"},
        {"action": "update"},
    ]
    assert "complete_rewrite_preserving_existing_user_facts" in existing["expected_behavior"]["must_include"]
    assert "append_increment_or_supplement" in existing["expected_behavior"]["must_not"]
    assert existing["expected_behavior"]["requires_final_response_after_tools"] is True

    for suite in ("pregnancy_diary_opt_out", "pregnancy_diary_negative", "pregnancy_diary_plan_intent"):
        case = by_suite[suite]
        assert case["expected_tool_calls"] == []
        assert {call["contract"] for call in case["forbidden_tool_calls"]} == {diary_manage}
        assert "claim_diary_saved" in case["expected_behavior"]["must_not"]

    mixed = by_suite["pregnancy_diary_health_mixed"]
    assert {call["contract"] for call in mixed["expected_tool_calls"]} == {diary_manage}
    assert mixed["expected_behavior"]["service_skill_id"] == "health-consultation"
    assert mixed["expected_behavior"]["requires_final_response_after_tools"] is True
    assert "continue_health_consultation_after_write" in mixed["expected_behavior"]["must_include"]
    assert "stop_after_diary_success" in mixed["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_uses_current_pregnancy_artifact_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    birth_prep_contracts = {tool_call["contract"] for tool_call in by_suite["birth_prep"]["expected_tool_calls"]}
    communication_contracts = {tool_call["contract"] for tool_call in by_suite["labor_communication"]["expected_tool_calls"]}

    assert "load_service_skill" in birth_prep_contracts
    assert "hospital_bag_form_create" in birth_prep_contracts
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


def test_product_agent_eval_seed_covers_working_context_and_durable_workflows() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    continuity = by_suite["working_context_multi_skill_continuity"]
    assert continuity["expected_tool_calls"] == []
    assert continuity["forbidden_tool_calls"] == [{"contract": "load_service_skill"}]
    assert continuity["input"]["fixtures"]["working_context"]["skills"] == [
        "milk-management",
        "device-guidance",
    ]

    redis_loss = by_suite["working_context_redis_loss_reload"]
    assert [call["contract"] for call in redis_loss["expected_tool_calls"]] == [
        "load_service_skill",
        "records.milk_status.read",
    ]
    assert redis_loss["input"]["fixtures"]["working_context_redis_available"] is False

    device = by_suite["device_unboxing_step_continuation"]
    assert device["input"]["messages"][-1]["content"] == "继续"
    assert device["expected_tool_calls"] == [
        {
            "contract": "devices.unboxing.advance",
            "timing": "after_user_completes_current_step",
            "args_subset": {"action": "complete_current"},
        }
    ]

    incomplete_device = by_suite["device_unboxing_incomplete_step"]
    assert incomplete_device["expected_tool_calls"] == []
    assert incomplete_device["forbidden_tool_calls"] == [{"contract": "devices.unboxing.advance"}]

    hospital_bag = by_suite["hospital_bag_form_to_card_workflow"]
    assert [call["contract"] for call in hospital_bag["expected_tool_calls"]] == [
        "load_service_skill",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
    ]

    pregnancy = by_suite["pregnancy_plan_end_to_end_workflow"]
    assert [call["contract"] for call in pregnancy["expected_tool_calls"]] == [
        "load_service_skill",
        "pregnancy.plan_intake.start",
        "pregnancy.plan_intake.analyze",
        "pregnancy.plan_intake.advance",
        "pregnancy.plan.propose",
    ]
    assert "internal_workflow_artifact" in pregnancy["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_splits_device_hazard_from_ibclc_artifact_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    support_contracts = {tool_call["contract"] for tool_call in by_suite["device_support_handoff"]["expected_tool_calls"]}
    ibclc_contracts = {tool_call["contract"] for tool_call in by_suite["ibclc_consult"]["expected_tool_calls"]}

    assert support_contracts == set()
    assert {call["contract"] for call in by_suite["device_support_handoff"]["forbidden_tool_calls"]} == {
        "devices.guidance.read",
        "support.ticket.propose",
    }
    assert ibclc_contracts == {"ibclc_consult_card_create"}
    assert by_suite["ibclc_consult"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["ibclc_consult"]["forbidden_tool_calls"] == [{"contract": "support.ticket.propose"}]


def test_product_agent_eval_seed_does_not_reference_missing_device_tool_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    clarify_contracts = {tool_call["contract"] for tool_call in by_suite["device_guidance"]["expected_tool_calls"]}
    known_device_contracts = {tool_call["contract"] for tool_call in by_suite["device_known_guidance"]["expected_tool_calls"]}

    assert "device_reference_lookup" not in clarify_contracts | known_device_contracts
    assert "devices.pump_status.read" not in clarify_contracts
    assert by_suite["device_guidance"]["expected_behavior"]["must_clarify"] == ["device_model", "first_use_context"]
    assert "devices.pump_status.read" in known_device_contracts
    assert "devices.guidance.read" in known_device_contracts
    assert by_suite["device_known_guidance"]["expected_behavior"]["requires_confirmation_before_write"] is False


def test_product_agent_eval_seed_keeps_memory_writes_off_live_run_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    memory_contracts = {tool_call["contract"] for tool_call in by_suite["memory_preference_capture"]["expected_tool_calls"]}
    forbidden_preference_contracts = {tool_call["contract"] for tool_call in by_suite["memory_preference_capture"]["forbidden_tool_calls"]}
    forbidden_sensitive_contracts = {tool_call["contract"] for tool_call in by_suite["memory_sensitive_rejection"]["forbidden_tool_calls"]}

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
    assert {"profile.read", "plans.current.read"} <= checkin_contracts
    assert "pregnancy_diary.manage" not in checkin_contracts
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["service_skill_id"] == "health-consultation"
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in by_suite["postpartum_recovery_task"]["expected_behavior"]["must_not"]
    assert "plans.task_create.propose" in task_contracts


def test_product_agent_eval_seed_covers_critical_health_and_emotion_regressions() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    for suite in ("health_consultation", "infant_health_red_flag", "emotion_support", "emotion_harm_baby"):
        assert by_suite[suite]["expected_tool_calls"] == []
        assert by_suite[suite]["expected_behavior"]["requires_confirmation_before_write"] is False
        assert by_suite[suite]["expected_behavior"]["forbids_side_effects"] is True

    web_search = by_suite["complex_health_web_search"]
    assert web_search["expected_tool_calls"] == []
    assert web_search["expected_events"] == [
        {"type": "CUSTOM", "name": "momcozy.agent.web_search"},
        {"type": "CUSTOM", "name": "momcozy.web_search.citations"},
    ]


def test_product_agent_eval_seed_loader_rejects_missing_required_suite() -> None:
    payload = {
        "schema_version": "agent_eval_seed.v2",
        "cases": [
            {
                "suite": "birth_prep",
                "name": "incomplete seed",
                "domain": "birth_prep",
                "input": {"messages": []},
                "expected_behavior": {"intent": "birth_prep", "route": "structured_service", "requires_confirmation_before_write": True},
                "expected_tool_calls": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="missing required suites"):
        validate_product_agent_eval_seed_payload(payload)
