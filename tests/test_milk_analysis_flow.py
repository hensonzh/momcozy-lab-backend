import pytest

from app.agents.cozymate.tools.milk_analysis_flow import (
    MILK_ANALYSIS_FIELDS,
    MilkAnalysisFlowError,
    advance_milk_analysis_intake,
    build_milk_analysis_assessment,
    initialize_milk_analysis_intake,
)


def _records_snapshot() -> dict:
    return {
        "window": {"days": 7},
        "status": {"data_coverage": "ready", "pumping_trend": "decreasing"},
        "counts": {"recent_feedings": 5, "recent_pumpings": 8, "recent_growth": 1},
        "volumes": {"recent_pumped_volume_ml": 1960.0},
        "pumping_trends": [
            {"date": "2026-07-12", "pumped_milk_volume_ml": 280.0, "pumping_count": 5},
        ],
        "analysis": {"data_coverage": "ready", "pumping_trend": "decreasing"},
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


def test_six_step_intake_is_ordered_and_advances_the_current_answer() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())

    assert [item["id"] for item in workflow["checklist"]] == list(MILK_ANALYSIS_FIELDS)
    assert workflow["checklist"][0]["status"] == "collected"
    assert workflow["current_field"] == "infant_wet_diapers"
    assert workflow["progress"] == {"index": 2, "total": 6, "completed_count": 1, "remaining_count": 5}

    advanced = advance_milk_analysis_intake(workflow, answer="24 小时有 7 片湿尿布")

    assert advanced["answers"] == {"infant_wet_diapers": "24 小时有 7 片湿尿布"}
    assert advanced["current_field"] == "infant_state_or_satisfaction"
    assert advanced["progress"]["index"] == 3


def test_intake_absorbs_multiple_model_classified_answers_from_one_turn() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())

    advanced = advance_milk_analysis_intake(
        workflow,
        answers={
            "infant_wet_diapers": "近 24 小时有 7 片湿尿布",
            "infant_state_or_satisfaction": "精神很好，吃完能安稳",
            "infant_growth_signal": "最近体重增长正常",
        },
    )

    assert advanced["answers"] == {
        "infant_wet_diapers": "近 24 小时有 7 片湿尿布",
        "infant_state_or_satisfaction": "精神很好，吃完能安稳",
        "infant_growth_signal": "最近体重增长正常",
    }
    assert advanced["current_field"] == "maternal_red_flags"
    assert advanced["progress"] == {"index": 5, "total": 6, "completed_count": 4, "remaining_count": 2}


def test_later_partial_safety_answer_preserves_previously_observed_red_flags() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    workflow = advance_milk_analysis_intake(
        workflow,
        answers={
            "infant_wet_diapers": "近 24 小时有 7 片湿尿布",
            "infant_state_or_satisfaction": "精神很好，吃完能安稳",
            "infant_growth_signal": "最近体重增长正常",
            "maternal_red_flags": "有寒战和硬块",
        },
    )

    completed = advance_milk_analysis_intake(
        workflow,
        answers={
            "maternal_red_flags": "没有发烧",
            "maternal_breast_comfort": "吸完舒服些",
        },
    )
    assessment = build_milk_analysis_assessment(completed)

    assert "有寒战和硬块" in completed["answers"]["maternal_red_flags"]
    assert "没有发烧" in completed["answers"]["maternal_red_flags"]
    assert assessment["risk"]["maternal_red_flags"] is True


def test_explicit_safety_correction_can_replace_previous_red_flag_evidence() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    workflow = advance_milk_analysis_intake(
        workflow,
        answers={
            "infant_wet_diapers": "近 24 小时有 7 片湿尿布",
            "infant_state_or_satisfaction": "精神很好，吃完能安稳",
            "infant_growth_signal": "最近体重增长正常",
            "maternal_red_flags": "有寒战和硬块",
        },
    )

    completed = advance_milk_analysis_intake(
        workflow,
        answers={
            "maternal_red_flags": "我刚才说错了，其实没有寒战和硬块",
            "maternal_breast_comfort": "吸完舒服些",
        },
    )
    assessment = build_milk_analysis_assessment(completed)

    assert completed["answers"]["maternal_red_flags"] == "我刚才说错了，其实没有寒战和硬块"
    assert assessment["risk"]["maternal_red_flags"] is False


def test_partial_safety_correction_does_not_clear_other_previous_red_flags() -> None:
    workflow = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    workflow = advance_milk_analysis_intake(
        workflow,
        answers={
            "infant_wet_diapers": "近 24 小时有 7 片湿尿布",
            "infant_state_or_satisfaction": "精神很好，吃完能安稳",
            "infant_growth_signal": "最近体重增长正常",
            "maternal_red_flags": "有发烧和硬块",
        },
    )

    completed = advance_milk_analysis_intake(
        workflow,
        answers={
            "maternal_red_flags": "纠正一下，其实没有发烧",
            "maternal_breast_comfort": "吸完舒服些",
        },
    )
    assessment = build_milk_analysis_assessment(completed)

    assert "有发烧和硬块" in completed["answers"]["maternal_red_flags"]
    assert assessment["risk"]["maternal_red_flags"] is True


def test_assessment_requires_complete_intake_and_returns_analysis_only() -> None:
    incomplete = initialize_milk_analysis_intake(records_snapshot=_records_snapshot())
    with pytest.raises(MilkAnalysisFlowError, match="milk_analysis_intake_incomplete"):
        build_milk_analysis_assessment(incomplete)

    first = build_milk_analysis_assessment(_complete_intake())

    assert first["findings"] == {
        "data_coverage": "ready",
        "pumping_trend": "decreasing",
    }
    assert "analysis_context_fingerprint" not in first
    assert "plan_decision" not in first
    assert first["card"]["title"] == "奶量分析"
    assert "can_start_plan" not in first["card"]
    assert "recommended_direction" not in first["card"]
    assert first["card"]["headline"]
    assert [section["id"] for section in first["card"]["sections"]] == ["milk", "signals", "next"]


def test_explicit_maternal_red_flags_are_reported_but_negation_is_not() -> None:
    safe = build_milk_analysis_assessment(_complete_intake())
    unsafe = build_milk_analysis_assessment(_complete_intake(red_flags="有发热，右侧乳房红肿而且越来越痛"))

    assert safe["risk"]["maternal_red_flags"] is False
    assert unsafe["risk"]["maternal_red_flags"] is True
    assert "需要优先处理" in unsafe["card"]["headline"]


@pytest.mark.parametrize(
    "red_flags",
    [
        "没有发热、寒战，乳房有硬块",
        "没有发烧和寒战，有红肿",
        "无发热，但右侧乳房越来越痛",
        "没有发热，不过寒战",
        "无发热，但疼痛加重",
    ],
)
def test_mixed_negated_and_positive_maternal_red_flags_are_still_reported(red_flags: str) -> None:
    assessment = build_milk_analysis_assessment(_complete_intake(red_flags=red_flags))

    assert assessment["risk"]["maternal_red_flags"] is True


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


def test_infant_signal_negation_does_not_create_a_false_safety_block() -> None:
    assessment = build_milk_analysis_assessment(_complete_intake(wet_diapers="尿布没有明显变少，24 小时大约 7 片"))

    assert assessment["risk"]["infant_intake_risk"] is False
