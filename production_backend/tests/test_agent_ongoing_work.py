from datetime import datetime, timezone
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentWorkflowState
from production_backend.app.modules.agent_runtime.run_lifecycle.ongoing_work import project_ongoing_work


def test_pregnancy_plan_projection_exposes_only_progress_and_next_step() -> None:
    workflow = _workflow(
        workflow_type="pregnancy_plan",
        active_step="personalized_followup",
        state={
            "phase": "personalized_followup",
            "personalized_followup_records": [{"topic": "sleep", "answer": "private answer"}],
            "visible_question": "最近睡眠最困扰你的是什么？",
            "plan_context": {"medical_notes": "private medical detail"},
        },
    )

    projected = project_ongoing_work([workflow], resident_skill_ids={"birth-prep"})

    assert projected == [
        {
            "name": "孕期计划",
            "progress": "基础信息已提交，个性化分析已完成 1 轮。",
            "next_step": "最近睡眠最困扰你的是什么？",
        }
    ]
    assert "private answer" not in str(projected)
    assert "private medical detail" not in str(projected)
    assert "workflow_id" not in str(projected)


def test_ongoing_work_requests_skill_reload_without_losing_business_progress() -> None:
    workflow = _workflow(
        workflow_type="device_unboxing",
        active_step="guide.charging",
        state={"phase": "guiding", "device_model": "Air1", "completed_steps": ["guide.parts", "guide.controls"]},
    )

    projected = project_ongoing_work([workflow], resident_skill_ids=set())

    assert projected == [
        {
            "name": "Air1 开箱指导",
            "progress": "已完成 2 个主步骤，当前停留在 guide.charging。",
            "next_step": "先调用 load_service_skill 加载 device-guidance；然后读取 guide.charging 的设备指导资料并继续。",
        }
    ]


def test_ongoing_work_supports_multiple_known_workflows_and_ignores_internal_types() -> None:
    workflows = [
        _workflow(
            workflow_type="hospital_bag",
            active_step="collecting_intake",
            state={"phase": "collecting_intake"},
        ),
        _workflow(
            workflow_type="device_unboxing",
            active_step="guide.parts",
            state={"phase": "guiding", "device_model": "Air1", "completed_steps": []},
        ),
        _workflow(workflow_type="internal_test", active_step="debug", state={"secret": "do not expose"}),
    ]

    projected = project_ongoing_work(workflows, resident_skill_ids={"birth-prep", "device-guidance"})

    assert [item["name"] for item in projected] == ["待产包", "Air1 开箱指导"]
    assert projected[0]["next_step"] == "等待用户提交待产包基础信息表单。"
    assert projected[1]["next_step"] == "读取 guide.parts 的设备指导资料并继续。"


def test_milk_analysis_projection_keeps_the_current_question_available_for_recovery() -> None:
    workflow = _workflow(
        workflow_type="milk_analysis",
        active_step="diaper_output",
        state={
            "phase": "collecting_intake",
            "current_field": "diaper_output",
            "next_question": "宝宝最近 24 小时大约有几片湿尿布？",
            "answers": {"records": "private answer"},
        },
    )

    projected = project_ongoing_work([workflow], resident_skill_ids={"milk-management"})

    assert projected == [
        {
            "name": "奶量分析",
            "progress": "奶量分析信息仍在采集中。",
            "next_step": "宝宝最近 24 小时大约有几片湿尿布？",
        }
    ]
    assert "private answer" not in str(projected)


def test_milk_analysis_projection_omits_completed_assessments() -> None:
    workflow = _workflow(
        workflow_type="milk_analysis",
        active_step="assessment_complete",
        state={
            "phase": "assessment_complete",
            "assessment": {"plan_decision": {"can_start_plan": True}},
        },
    )

    assert project_ongoing_work([workflow], resident_skill_ids={"milk-management"}) == []


def _workflow(*, workflow_type: str, active_step: str, state: dict) -> AgentWorkflowState:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    return AgentWorkflowState(
        id=uuid4(),
        thread_id=uuid4(),
        owner_user_id=uuid4(),
        run_id=uuid4(),
        workflow_type=workflow_type,
        status="collecting",
        schema_version="v1",
        state=state,
        active_step=active_step,
        created_at=now,
        updated_at=now,
    )
