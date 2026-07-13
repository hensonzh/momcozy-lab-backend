import pytest

from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.milk_analysis_flow import (
    MILK_ANALYSIS_FIELDS,
    MilkAnalysisFlowError,
    advance_milk_analysis_intake,
    build_milk_analysis_assessment,
    initialize_milk_analysis_intake,
)


def _records_snapshot(*, status: str = "under_supply_alert") -> dict:
    return {
        "window": {"days": 7},
        "status": status,
        "counts": {"recent_feedings": 5, "recent_pumpings": 8, "recent_growth": 1},
        "volumes": {"recent_pumped_volume_ml": 1960.0},
        "pumping_trends": [
            {"date": "2026-07-12", "pumped_milk_volume_ml": 280.0, "pumping_count": 5},
        ],
        "analysis": {"status": status, "growth_observation": "stable"},
    }


def _complete_intake(
    *,
    red_flags: str = "没有发热、寒战、红肿、硬块或疼痛加重",
    wet_diapers: str = "24 小时有 7 片湿尿布",
) -> dict:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    answers = {
        "infant_wet_diapers": wet_diapers,
        "infant_state_or_satisfaction": "精神不错，吃奶后能安稳",
        "infant_growth_signal": "最近体重增长正常",
        "maternal_red_flags": red_flags,
        "maternal_breast_comfort": "吸完后舒服，没有持续胀痛",
    }
    for field in MILK_ANALYSIS_FIELDS[1:]:
        workflow = advance_milk_analysis_intake(workflow, answer=answers[field])
    return workflow


def test_six_step_intake_is_ordered_and_only_advances_one_answer_at_a_time() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())

    assert [item["id"] for item in workflow["checklist"]] == list(MILK_ANALYSIS_FIELDS)
    assert workflow["checklist"][0]["status"] == "collected"
    assert workflow["current_field"] == "infant_wet_diapers"
    assert workflow["progress"] == {"index": 2, "total": 6, "completed_count": 1, "remaining_count": 5}

    advanced = advance_milk_analysis_intake(workflow, answer="24 小时有 7 片湿尿布")

    assert advanced["answers"] == {"infant_wet_diapers": "24 小时有 7 片湿尿布"}
    assert advanced["current_field"] == "infant_state_or_satisfaction"
    assert advanced["progress"]["index"] == 3


def test_assessment_requires_complete_intake_and_fingerprints_the_exact_context() -> None:
    incomplete = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    with pytest.raises(MilkAnalysisFlowError, match="milk_analysis_intake_incomplete"):
        build_milk_analysis_assessment(incomplete)

    first = build_milk_analysis_assessment(_complete_intake())
    second_workflow = _complete_intake()
    second_workflow["answers"]["maternal_breast_comfort"] = "吸完后还是有一点胀"
    second = build_milk_analysis_assessment(second_workflow)

    assert first["analysis_context_fingerprint"]
    assert first["analysis_context_fingerprint"] != second["analysis_context_fingerprint"]
    assert first["plan_decision"] == {
        "can_start_plan": True,
        "recommended_direction": "increase",
        "reason": "recent_milk_below_expected_without_safety_block",
    }
    assert first["card"]["title"] == "奶量分析"
    assert "analysis_context_fingerprint" not in first["card"]
    assert first["card"]["headline"]
    assert [section["id"] for section in first["card"]["sections"]] == ["milk", "signals", "next"]


def test_explicit_maternal_red_flags_block_plan_eligibility_but_negation_does_not() -> None:
    safe = build_milk_analysis_assessment(_complete_intake())
    unsafe = build_milk_analysis_assessment(_complete_intake(red_flags="有发热，右侧乳房红肿而且越来越痛"))

    assert safe["risk"]["maternal_red_flags"] is False
    assert safe["plan_decision"]["can_start_plan"] is True
    assert unsafe["risk"]["maternal_red_flags"] is True
    assert unsafe["plan_decision"] == {
        "can_start_plan": False,
        "recommended_direction": None,
        "reason": "maternal_red_flags_require_professional_support",
    }


@pytest.mark.parametrize(
    "red_flags",
    [
        "没有发热、寒战，乳房有硬块",
        "没有发烧和寒战，有红肿",
        "无发热，但右侧乳房越来越痛",
    ],
)
def test_mixed_negated_and_positive_maternal_red_flags_still_block_plan(red_flags: str) -> None:
    assessment = build_milk_analysis_assessment(_complete_intake(red_flags=red_flags))

    assert assessment["risk"]["maternal_red_flags"] is True
    assert assessment["plan_decision"]["can_start_plan"] is False


@pytest.mark.parametrize(
    "red_flags",
    [
        "没有发热、寒战、红肿、硬块或疼痛加重",
        "没有发热，寒战、红肿和硬块也都没有",
        "乳房没有红肿或硬块",
        "目前不发热，也没有寒战或硬块",
    ],
)
def test_maternal_red_flag_negation_can_cover_an_explicit_symptom_list(red_flags: str) -> None:
    assessment = build_milk_analysis_assessment(_complete_intake(red_flags=red_flags))

    assert assessment["risk"]["maternal_red_flags"] is False
    assert assessment["plan_decision"]["can_start_plan"] is True


def test_infant_signal_negation_does_not_create_a_false_safety_block() -> None:
    assessment = build_milk_analysis_assessment(_complete_intake(wet_diapers="尿布没有明显变少，24 小时大约 7 片"))

    assert assessment["risk"]["infant_intake_risk"] is False
    assert assessment["plan_decision"]["can_start_plan"] is True
