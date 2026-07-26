from pathlib import Path

import pytest

from app.agents.cozymate.evals import (
    REQUIRED_PRODUCT_AGENT_EVAL_SUITES,
    load_product_agent_eval_seed_cases,
    validate_product_agent_eval_seed_payload,
)
from app.agents.cozymate.tools import default_tool_registry


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_product_agent_eval_seed_covers_required_suites() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    suites = {case["suite"] for case in cases}

    assert set(REQUIRED_PRODUCT_AGENT_EVAL_SUITES) <= suites
    assert len(cases) >= len(REQUIRED_PRODUCT_AGENT_EVAL_SUITES)


def test_product_agent_eval_seed_covers_append_only_context_regressions() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    followup = by_suite["context_append_tool_followup"]
    multimodal = by_suite["context_append_multimodal_tool_result"]

    assert followup["input"]["fixtures"]["ordered_context_items"][1]["type"] == "function_call_output"
    assert "repeat_profile_read" in followup["expected_behavior"]["must_not"]
    output = multimodal["input"]["fixtures"]["ordered_context_items"][0]["output"]
    assert [block["type"] for block in output] == ["input_text", "input_image"]


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
            "device-guidance",
        }
        assert isinstance(behavior["requires_confirmation_before_write"], bool)
        assert "messages" in case["input"]
        assert isinstance(case["expected_tool_calls"], list)


def test_product_agent_eval_seed_references_only_registered_tool_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    registered = set(default_tool_registry().names_for_sdk())

    referenced = {
        tool_call["contract"]
        for case in cases
        for field in ("expected_tool_calls", "forbidden_tool_calls")
        for tool_call in case.get(field, [])
    }

    assert referenced <= registered


def test_product_agent_eval_seed_mutation_calls_always_assert_operation() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    for case in cases:
        for tool_call in case["expected_tool_calls"]:
            if tool_call["contract"].endswith(("_mutate", "_create", "_update", "_delete")):
                assert "operation" in tool_call.get("args_subset", {}), (
                    case["suite"],
                    tool_call["contract"],
                )


def test_product_agent_eval_seed_separates_timeline_facts_from_milk_analysis() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    milk_cases = {case["suite"]: case for case in cases if case["suite"] in {"milk_daily_summary", "milk_trend_analysis"}}

    assert set(milk_cases) == {"milk_daily_summary", "milk_trend_analysis"}
    assert {call["contract"] for call in milk_cases["milk_daily_summary"]["expected_tool_calls"]} == {
        "schedule_timeline_read"
    }
    assert milk_cases["milk_trend_analysis"]["expected_tool_calls"] == [
        {
            "contract": "milk_analysis_manage",
            "timing": "before_final_response",
            "args_subset": {
                "operation": "review",
                "detail_level": "summary",
                "days": 14,
            },
        }
    ]


def test_product_agent_eval_seed_requires_measured_lactation_task_completion() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    measured = by_suite["milk_task_completion_measured"]
    missing = by_suite["milk_task_completion_missing_volume"]

    assert measured["expected_tool_calls"] == [
        {
            "contract": "schedule_timeline_mutate",
            "timing": "after_exact_target_resolution",
            "args_subset": {
                "operation": "set_status",
                "entry_type": "schedule",
                "task_id": "55555555-5555-4555-8555-555555555555",
                "completed": True,
                "occurred_at": "2026-07-24T08:55:00+08:00",
                "milk_volume_ml": 105,
            },
        }
    ]
    assert "single_record_action" in measured["expected_behavior"]["must_include"]
    assert missing["expected_tool_calls"] == []
    assert missing["forbidden_tool_calls"] == [{"contract": "schedule_timeline_mutate"}]
    assert "ask_actual_milk_volume" in missing["expected_behavior"]["must_include"]


def test_product_agent_eval_seed_keeps_pump_recommendation_read_only_and_model_owned() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    case = next(case for case in cases if case["suite"] == "device_pump_recommendation")

    assert case["expected_tool_calls"] == [
        {
            "contract": "pump_models_read",
            "timing": "before_recommendation",
            "args_subset": {},
        }
    ]
    assert case["forbidden_tool_calls"] == [{"contract": "hospital_bag_cart_mutate"}]
    assert "model_selected_recommendation" in case["expected_behavior"]["must_include"]
    assert "tool_selected_recommendation" in case["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_uses_current_milk_action_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    schedule_contracts = {tool_call["contract"] for tool_call in by_suite["milk_schedule_management"]["expected_tool_calls"]}
    plan_contracts = {tool_call["contract"] for tool_call in by_suite["milk_plan_creation"]["expected_tool_calls"]}

    assert schedule_contracts == {"schedule_timeline_mutate"}
    assert by_suite["milk_schedule_management"]["expected_tool_calls"][0]["args_subset"] == {
        "entry_type": "schedule",
        "operation": "reschedule",
    }
    assert "plan_task_update_proposal" not in schedule_contracts
    assert plan_contracts == {"milk_analysis_manage", "plan_mutate"}
    analysis_calls = [
        tool_call
        for tool_call in by_suite["milk_plan_creation"]["expected_tool_calls"]
        if tool_call["contract"] == "milk_analysis_manage"
    ]
    assert [tool_call["args_subset"]["operation"] for tool_call in analysis_calls] == [
        "start_or_resume",
        "evaluate",
    ]
    assert "milk_plan_proposal" not in plan_contracts
    assert by_suite["milk_plan_creation"]["expected_tool_calls"][-1]["args_subset"] == {
        "operation": "create",
        "plan_type": "milk_management",
    }


def test_product_agent_eval_seed_covers_profile_read() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}
    case = by_suite["profile_read"]

    assert {call["contract"] for call in case["expected_tool_calls"]} == {"profile_read"}
    assert case["expected_behavior"]["requires_confirmation_before_write"] is False
    assert len(case["input"]["fixtures"]["current_infants"]) == 2
    assert "separate_context_for_each_birth_order" in case["expected_behavior"]["must_include"]
    assert "stable_infant_id_for_each_infant" in case["expected_behavior"]["must_include"]
    assert "sex_at_birth_for_each_infant" in case["expected_behavior"]["must_include"]
    assert "stable_machine_readable_missing_and_quality_codes" in case["expected_behavior"]["must_include"]
    assert {infant["infant_profile"]["sex_at_birth"] for infant in case["input"]["fixtures"]["current_infants"]} == {"female", "male"}
    assert "return_delivery_history" in case["expected_behavior"]["must_not"]
    assert "estimated_due_date_hidden_after_actual_delivery" in case["expected_behavior"]["must_include"]
    assert "dynamic_path_strings_in_missing_or_quality_fields" in case["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_uses_current_pregnancy_action_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    plan_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_creation"]["expected_tool_calls"]}
    intake_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_intake_start"]["expected_tool_calls"]}
    analysis_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_intake_analysis"]["expected_tool_calls"]}
    followup_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_plan_personalized_followup"]["expected_tool_calls"]}
    task_contracts = {tool_call["contract"] for tool_call in by_suite["pregnancy_task_completion"]["expected_tool_calls"]}
    diary_contracts = {tool_call["contract"] for tool_call in by_suite["diary_pregnancy_entry"]["expected_tool_calls"]}

    assert plan_contracts == {"plan_mutate"}
    assert by_suite["pregnancy_plan_creation"]["expected_tool_calls"][-1]["args_subset"] == {
        "operation": "create",
        "plan_type": "pregnancy",
    }
    assert by_suite["pregnancy_plan_creation"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in by_suite["pregnancy_plan_creation"]["expected_behavior"]["must_not"]
    assert intake_contracts == {"pregnancy_intake_manage"}
    assert by_suite["pregnancy_plan_intake_start"]["expected_tool_calls"][-1]["args_subset"] == {
        "command": "start_or_resume"
    }
    assert analysis_contracts == {"pregnancy_intake_manage"}
    assert by_suite["pregnancy_plan_intake_analysis"]["expected_tool_calls"][0]["args_subset"] == {
        "command": "submit_form"
    }
    assert followup_contracts == {"pregnancy_intake_manage"}
    assert by_suite["pregnancy_plan_personalized_followup"]["expected_tool_calls"][0]["args_subset"] == {
        "command": "answer_current"
    }
    assert by_suite["pregnancy_plan_intake_analysis"]["expected_behavior"]["must_include"] == [
        "plain_language_acknowledgement",
        "one_followup_question",
        "dynamic_zero_to_three_followups",
    ]
    assert "medical_jargon_pileup" in by_suite["pregnancy_plan_intake_analysis"]["expected_behavior"]["must_not"]
    assert by_suite["pregnancy_plan_personalized_followup"]["expected_behavior"]["must_include"] == [
        "plain_language_bridge",
        "one_followup_question",
        "workflow_continuity",
    ]
    assert "compound_followup_question" in by_suite["pregnancy_plan_personalized_followup"]["expected_behavior"]["must_not"]
    assert by_suite["pregnancy_plan_personalized_followup"]["forbidden_tool_calls"] == [
        {
            "contract": "pregnancy_intake_manage",
            "args_subset": {"command": "submit_form"},
        },
        {
            "contract": "plan_mutate",
            "args_subset": {"operation": "create", "plan_type": "pregnancy"},
        },
    ]
    attachment_guard = by_suite["pregnancy_plan_checkup_attachment_guard"]
    assert attachment_guard["input"]["fixtures"]["current_run_authenticated_attachments"] == []
    assert "mark_uploaded_from_text_only" in attachment_guard["expected_behavior"]["must_not"]
    assert by_suite["pregnancy_plan_intake_start"]["forbidden_tool_calls"] == [
        {
            "contract": "plan_mutate",
            "args_subset": {"operation": "create", "plan_type": "pregnancy"},
        }
    ]
    assert by_suite["pregnancy_plan_intake_analysis"]["forbidden_tool_calls"] == [
        {
            "contract": "plan_mutate",
            "args_subset": {"operation": "create", "plan_type": "pregnancy"},
        }
    ]
    assert "schedule_timeline_mutate" in task_contracts
    assert diary_contracts == {"diary_mutate"}
    assert by_suite["diary_pregnancy_entry"]["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"
    assert by_suite["diary_pregnancy_entry"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["general_diary_entry"]["expected_tool_calls"] == [
        {
            "contract": "diary_mutate",
            "timing": "after_user_record_intent",
            "args_subset": {
                "operation": "create",
            },
        }
    ]
    assert "pregnancy_plan_proposal" not in plan_contracts
    assert "plan_task_update_proposal" not in task_contracts
    assert "diary_entry_upsert_proposal" not in diary_contracts

    diary_delete = by_suite["diary_mutate_exact"]
    assert diary_delete["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert diary_delete["input"]["fixtures"]["explicit_delete_intent"] is True
    assert "oral_confirmation_complete" not in diary_delete["input"]["fixtures"]
    assert "redundant_confirmation_question" in diary_delete["expected_behavior"]["must_not"]
    assert diary_delete["expected_tool_calls"] == [
        {
            "contract": "diary_mutate",
            "timing": "on_explicit_intent_and_exact_target",
            "args_subset": {
                "operation": "delete",
                "entry_date": "2026-07-04",
                "confirmation_evidence": "Delete the July 4 pregnancy diary entry now.",
            },
        }
    ]
    assert by_suite["diary_mutate_ambiguous"]["expected_tool_calls"] == []
    assert by_suite["diary_mutate_ambiguous"]["forbidden_tool_calls"] == [{"contract": "diary_mutate"}]

    plan_delete = by_suite["pregnancy_plan_delete_exact"]
    assert plan_delete["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert plan_delete["input"]["fixtures"]["explicit_delete_intent"] is True
    assert "oral_confirmation_complete" not in plan_delete["input"]["fixtures"]
    assert plan_delete["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "redundant_confirmation_question" in plan_delete["expected_behavior"]["must_not"]
    assert plan_delete["expected_tool_calls"] == [
        {
            "contract": "plan_mutate",
            "timing": "on_explicit_intent_and_exact_target",
            "args_subset": {
                "operation": "delete",
                "plan_id": "11111111-1111-4111-8111-111111111111",
            },
        }
    ]
    assert by_suite["pregnancy_plan_delete_ambiguous"]["expected_tool_calls"] == []
    assert by_suite["pregnancy_plan_delete_ambiguous"]["forbidden_tool_calls"] == [{"contract": "plan_mutate"}]

    task_exact = by_suite["pregnancy_task_completion"]
    assert task_exact["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert task_exact["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in task_exact["expected_behavior"]["must_not"]
    assert task_exact["expected_tool_calls"][0]["args_subset"]["operation"] == "set_status"
    assert by_suite["pregnancy_task_ambiguous"]["expected_tool_calls"] == []
    assert by_suite["pregnancy_task_ambiguous"]["forbidden_tool_calls"] == [
        {"contract": "schedule_timeline_mutate"}
    ]

    record_exact = by_suite["feeding_record_delete_exact"]
    assert record_exact["input"]["fixtures"]["trusted_exact_target"]["owner_scoped"] is True
    assert record_exact["expected_behavior"]["requires_confirmation_before_write"] is False
    assert record_exact["expected_tool_calls"][0]["args_subset"]["operation"] == "delete"
    assert by_suite["feeding_record_delete_ambiguous"]["expected_tool_calls"] == []


def test_product_agent_eval_seed_covers_resumable_pregnancy_workflow_controls() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    side_question = by_suite["pregnancy_plan_side_question_resume"]
    pause_resume = by_suite["pregnancy_plan_pause_resume"]
    edit_history = by_suite["pregnancy_plan_edit_history"]
    stale_command = by_suite["pregnancy_plan_stale_command_recovery"]

    assert side_question["expected_tool_calls"] == []
    assert side_question["input"]["fixtures"]["workflow_cursor_attached"] is False
    assert "preserve_workflow_revision" in side_question["expected_behavior"]["must_include"]
    assert side_question["forbidden_tool_calls"] == [
        {"contract": "pregnancy_intake_manage"}
    ]

    assert [call["args_subset"] for call in pause_resume["expected_tool_calls"]] == [
        {"command": "pause"},
        {"command": "resume"},
    ]
    assert [event["type"] for event in pause_resume["expected_events"]] == [
        "workflow.paused",
        "workflow.resumed",
    ]

    assert edit_history["expected_tool_calls"] == [
        {
            "contract": "pregnancy_intake_manage",
            "timing": "on_historical_choice",
            "args_subset": {
                "command": "edit_answer",
                "step_id": "checkup_done",
                "choice_id": "confirm_no_checkup_yet",
            },
        }
    ]
    assert "invalidate_dependent_steps" in edit_history["expected_behavior"]["must_include"]

    assert stale_command["expected_tool_calls"] == []
    assert stale_command["input"]["fixtures"]["workflow_cursor_is_stale"] is True
    assert "no_state_mutation" in stale_command["expected_behavior"]["must_include"]
    assert stale_command["forbidden_tool_calls"] == [
        {"contract": "pregnancy_intake_manage"}
    ]


def test_product_agent_eval_seed_covers_implicit_opt_out_negative_and_health_mixed_diary_parity() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}
    diary_save = "diary_mutate"

    implicit = by_suite["diary_pregnancy_implicit_entry"]
    assert {call["contract"] for call in implicit["expected_tool_calls"]} == {diary_save}
    assert implicit["expected_tool_calls"][0]["args_subset"] == {"operation": "create"}
    assert "require_explicit_save_phrase" in implicit["expected_behavior"]["must_not"]
    assert implicit["expected_behavior"]["requires_confirmation_before_write"] is False

    for suite in ("diary_pregnancy_entry", "diary_pregnancy_implicit_entry", "diary_pregnancy_existing_entry"):
        assert by_suite[suite]["forbidden_tool_calls"] == []

    existing = by_suite["diary_pregnancy_existing_entry"]
    assert [call["contract"] for call in existing["expected_tool_calls"]] == [
        diary_save,
        "diary_read",
        diary_save,
    ]
    assert [call["args_subset"] for call in existing["expected_tool_calls"]] == [
        {"operation": "create"},
        {},
        {"operation": "update"},
    ]
    for case in cases:
        for call in case["expected_tool_calls"]:
            if call["contract"] in {"diary_read", "diary_mutate"}:
                assert "diary_type" not in call.get("args_subset", {})
    assert "complete_rewrite_preserving_existing_user_facts" in existing["expected_behavior"]["must_include"]
    assert "append_increment_or_supplement" in existing["expected_behavior"]["must_not"]
    assert existing["expected_behavior"]["requires_final_response_after_tools"] is True

    for suite in ("diary_pregnancy_opt_out", "diary_pregnancy_negative", "diary_pregnancy_plan_intent"):
        case = by_suite[suite]
        assert case["expected_tool_calls"] == []
        assert {call["contract"] for call in case["forbidden_tool_calls"]} == {diary_save}
        assert "claim_diary_saved" in case["expected_behavior"]["must_not"]

    mixed = by_suite["diary_pregnancy_health_mixed"]
    assert {call["contract"] for call in mixed["expected_tool_calls"]} == {diary_save}
    assert mixed["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"
    assert mixed["expected_behavior"]["requires_final_response_after_tools"] is True
    assert "continue_health_response_after_write" in mixed["expected_behavior"]["must_include"]
    assert "stop_after_diary_success" in mixed["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_uses_current_birth_prep_artifact_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    birth_prep_contracts = {tool_call["contract"] for tool_call in by_suite["birth_prep"]["expected_tool_calls"]}

    assert birth_prep_contracts == {"hospital_bag_manage"}
    assert "birth_prep_intake" not in birth_prep_contracts


def test_product_agent_eval_seed_uses_current_hospital_bag_action_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    cart_contracts = {tool_call["contract"] for tool_call in by_suite["hospital_bag_cart_mutate"]["expected_tool_calls"]}

    assert "hospital_bag_cart_mutate" in cart_contracts
    assert by_suite["hospital_bag_cart_mutate"]["expected_tool_calls"][0]["args_subset"] == {
        "operation": "reset_cart"
    }
    assert by_suite["hospital_bag_cart_mutate"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["hospital_bag_cart_mutate"]["expected_behavior"]["service_skill_id"] == "birth-prep"
    assert "hospital_bag_cart_mutate_proposal" not in cart_contracts


def test_product_agent_eval_seed_covers_ordered_ledger_and_durable_workflows() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    continuity = by_suite["ledger_multi_tool_continuity"]
    assert continuity["expected_tool_calls"] == []
    assert continuity["forbidden_tool_calls"] == []

    refresh = by_suite["ledger_latest_fact_refresh"]
    assert [call["contract"] for call in refresh["expected_tool_calls"]] == ["schedule_timeline_read"]
    assert refresh["input"]["fixtures"]["context_ledger_available"] is True

    device = by_suite["device_unboxing_step_continuation"]
    assert device["input"]["messages"][-1]["content"] == "继续"
    assert device["expected_tool_calls"] == [
        {
            "contract": "devices_guidance_manage",
            "timing": "after_user_completes_current_step",
            "args_subset": {"operation": "complete_current"},
        }
    ]

    incomplete_device = by_suite["device_unboxing_incomplete_step"]
    assert incomplete_device["expected_tool_calls"] == []
    assert incomplete_device["forbidden_tool_calls"] == [
        {
            "contract": "devices_guidance_manage",
            "args_subset": {"operation": "complete_current"},
        }
    ]

    incomplete_delivery = by_suite["device_unboxing_incomplete_delivery"]
    assert incomplete_delivery["input"]["messages"][-1]["content"] == "继续"
    assert incomplete_delivery["expected_tool_calls"] == []
    assert incomplete_delivery["forbidden_tool_calls"] == [
        {
            "contract": "devices_guidance_manage",
            "args_subset": {"operation": "complete_current"},
        }
    ]

    hospital_bag = by_suite["hospital_bag_form_to_card_workflow"]
    assert [call["contract"] for call in hospital_bag["expected_tool_calls"]] == [
        "hospital_bag_manage",
        "hospital_bag_manage",
    ]
    assert "deterministic_continuation_without_model" in hospital_bag["expected_behavior"]["must_include"]

    pregnancy = by_suite["pregnancy_plan_end_to_end_workflow"]
    assert [call["contract"] for call in pregnancy["expected_tool_calls"]] == [
        "pregnancy_intake_manage",
        "pregnancy_intake_manage",
        "pregnancy_intake_manage",
        "plan_mutate",
    ]
    assert [call.get("args_subset") for call in pregnancy["expected_tool_calls"]] == [
        {"command": "start_or_resume"},
        {"command": "submit_form"},
        {"command": "answer_current"},
        {"operation": "create", "plan_type": "pregnancy"},
    ]
    assert "internal_workflow_artifact" in pregnancy["expected_behavior"]["must_not"]


def test_product_agent_eval_seed_splits_device_hazard_from_ibclc_artifact_contract() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    support_contracts = {tool_call["contract"] for tool_call in by_suite["device_support_handoff"]["expected_tool_calls"]}
    ibclc_contracts = {tool_call["contract"] for tool_call in by_suite["ibclc_consult"]["expected_tool_calls"]}

    assert support_contracts == set()
    assert {call["contract"] for call in by_suite["device_support_handoff"]["forbidden_tool_calls"]} == {
        "devices_guidance_manage",
        "support_ticket_create",
    }
    assert ibclc_contracts == {"ibclc_consult_card_create"}
    assert by_suite["ibclc_consult"]["expected_tool_calls"][0]["args_subset"] == {
        "operation": "create"
    }
    assert by_suite["ibclc_consult"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert by_suite["ibclc_consult"]["forbidden_tool_calls"] == [{"contract": "support_ticket_create"}]


def test_product_agent_eval_seed_uses_only_supported_device_guidance_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    clarify_contracts = {tool_call["contract"] for tool_call in by_suite["device_guidance"]["expected_tool_calls"]}
    known_device_contracts = {tool_call["contract"] for tool_call in by_suite["device_known_guidance"]["expected_tool_calls"]}

    assert "device_reference_lookup" not in clarify_contracts | known_device_contracts
    assert by_suite["device_guidance"]["expected_behavior"]["must_clarify"] == ["device_model", "first_use_context"]
    assert known_device_contracts == {"devices_guidance_manage"}
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


def test_product_agent_eval_seed_covers_postpartum_recovery_main_agent_capability() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    checkin_contracts = {tool_call["contract"] for tool_call in by_suite["postpartum_recovery_checkin"]["expected_tool_calls"]}
    task_contracts = {tool_call["contract"] for tool_call in by_suite["postpartum_recovery_task"]["expected_tool_calls"]}

    assert by_suite["postpartum_recovery_checkin"]["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"
    assert by_suite["postpartum_recovery_checkin"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert {"profile_read", "schedule_timeline_read"} <= checkin_contracts
    assert "diary_mutate" not in checkin_contracts
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"
    assert by_suite["postpartum_recovery_task"]["expected_behavior"]["requires_confirmation_before_write"] is False
    assert "confirmation_card" in by_suite["postpartum_recovery_task"]["expected_behavior"]["must_not"]
    assert "schedule_timeline_mutate" in task_contracts
    assert by_suite["postpartum_recovery_task"]["expected_tool_calls"][-1]["args_subset"] == {
        "entry_type": "schedule",
        "operation": "create",
        "domain": "postpartum_recovery",
    }


def test_product_agent_eval_seed_covers_critical_health_and_emotion_regressions() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    for suite in ("maternal_health_red_flag", "infant_health_red_flag", "self_harm_safety", "baby_harm_safety"):
        assert by_suite[suite]["expected_tool_calls"] == []
        assert by_suite[suite]["expected_behavior"]["requires_confirmation_before_write"] is False
        assert by_suite[suite]["expected_behavior"]["forbids_side_effects"] is True
        assert by_suite[suite]["expected_behavior"]["service_skill_id"] == "cozymate_service_agent"

    web_search = by_suite["complex_health_web_search"]
    assert web_search["expected_tool_calls"] == []
    assert web_search["expected_events"] == [
        {"type": "CUSTOM", "name": "momcozy.agent.web_search"},
        {"type": "CUSTOM", "name": "momcozy.web_search.citations"},
    ]


def test_product_agent_eval_seed_covers_scope_workflow_length_and_prompt_confidentiality() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)
    by_suite = {case["suite"]: case for case in cases}

    assert by_suite["out_of_scope_response"]["expected_behavior"]["must_not"] == ["substantive_out_of_scope_answer"]
    assert "answer_maternal_infant_part" in by_suite["mixed_scope_response"]["expected_behavior"]["must_include"]
    assert "answer_current_request_first" in by_suite["workflow_detour"]["expected_behavior"]["must_include"]
    assert "advance_workflow_from_unrelated_content" in by_suite["workflow_detour"]["expected_behavior"]["must_not"]
    assert "resume_workflow_without_user_request" in by_suite["workflow_pause"]["expected_behavior"]["must_not"]
    assert by_suite["ordinary_response_conciseness"]["expected_behavior"]["target_max_chinese_characters"] == 200
    assert "reveal_transformed_hidden_instructions" in by_suite["prompt_confidentiality"]["expected_behavior"]["must_not"]


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
