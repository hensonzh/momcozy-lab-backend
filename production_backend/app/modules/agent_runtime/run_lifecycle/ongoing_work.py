from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..models import AgentWorkflowState


PREGNANCY_PLAN_WORKFLOW_TYPE = "pregnancy_plan"
HOSPITAL_BAG_WORKFLOW_TYPE = "hospital_bag"
DEVICE_UNBOXING_WORKFLOW_TYPE = "device_unboxing"
_ACTIVE_WORKFLOW_STATUSES = frozenset({"collecting", "ready", "waiting", "paused"})


def project_ongoing_work(
    workflow_states: Iterable[AgentWorkflowState],
    *,
    resident_skill_ids: set[str],
) -> list[dict[str, str]]:
    projectors: dict[str, Callable[[AgentWorkflowState, bool], dict[str, str] | None]] = {
        PREGNANCY_PLAN_WORKFLOW_TYPE: _project_pregnancy_plan,
        HOSPITAL_BAG_WORKFLOW_TYPE: _project_hospital_bag,
        DEVICE_UNBOXING_WORKFLOW_TYPE: _project_device_unboxing,
    }
    required_skills = {
        PREGNANCY_PLAN_WORKFLOW_TYPE: "birth-prep",
        HOSPITAL_BAG_WORKFLOW_TYPE: "birth-prep",
        DEVICE_UNBOXING_WORKFLOW_TYPE: "device-guidance",
    }
    projected: list[dict[str, str]] = []
    for workflow_state in workflow_states:
        if workflow_state.status not in _ACTIVE_WORKFLOW_STATUSES:
            continue
        projector = projectors.get(workflow_state.workflow_type)
        if projector is None:
            continue
        required_skill = required_skills[workflow_state.workflow_type]
        item = projector(workflow_state, required_skill in resident_skill_ids)
        if item is not None:
            projected.append(item)
    return projected


def _project_pregnancy_plan(workflow: AgentWorkflowState, skill_loaded: bool) -> dict[str, str]:
    state = _state(workflow)
    phase = _text(state, "phase") or workflow.active_step
    records = state.get("personalized_followup_records")
    followup_count = len(records) if isinstance(records, list) else 0
    if phase == "collecting_intake":
        progress = "孕期计划基础信息表单已创建，等待用户提交。"
        next_step = "等待用户提交孕期计划基础信息表单。"
    elif phase == "personalized_followup":
        progress = f"基础信息已提交，个性化分析已完成 {followup_count} 轮。"
        next_step = _text(state, "visible_question") or "继续当前个性化分析问题。"
    elif phase == "checkup_done_question":
        progress = "个性化分析已完成，正在确认用户是否做过产检。"
        next_step = _text(state, "visible_question") or "确认用户是否做过产检。"
    elif phase == "checkup_records_upload":
        progress = "个性化分析已完成，正在确认产检资料。"
        next_step = _text(state, "visible_question") or "确认是否上传产检资料或选择暂不上传。"
    elif phase == "final_plan_confirmation":
        progress = "基础信息和个性化分析已完成，等待生成计划前的最后补充。"
        next_step = _text(state, "visible_question") or "确认是否还有需要纳入计划的信息。"
    elif phase == "ready_to_generate":
        progress = "孕期计划所需信息已经收集完成。"
        next_step = "调用孕期计划生成工具创建最终计划。"
    else:
        progress = "孕期计划仍在进行中。"
        next_step = "根据当前流程阶段继续，不要重新开始信息采集。"
    return {
        "name": "孕期计划",
        "progress": progress,
        "next_step": _with_skill_reload(next_step, required_skill="birth-prep", skill_loaded=skill_loaded),
    }


def _project_hospital_bag(workflow: AgentWorkflowState, skill_loaded: bool) -> dict[str, str]:
    phase = _text(_state(workflow), "phase") or workflow.active_step
    if phase == "collecting_intake":
        progress = "待产包基础信息表单已创建，等待用户提交。"
        next_step = "等待用户提交待产包基础信息表单。"
    elif phase == "ready_to_generate":
        progress = "待产包基础信息已收集完成。"
        next_step = "调用待产包清单工具生成个性化清单。"
    elif phase == "reviewing":
        progress = "待产包清单已经生成，正在根据用户反馈调整。"
        next_step = "继续处理用户对当前清单的增删或预算调整。"
    else:
        progress = "待产包准备仍在进行中。"
        next_step = "根据当前流程阶段继续，不要重复创建表单。"
    return {
        "name": "待产包",
        "progress": progress,
        "next_step": _with_skill_reload(next_step, required_skill="birth-prep", skill_loaded=skill_loaded),
    }


def _project_device_unboxing(workflow: AgentWorkflowState, skill_loaded: bool) -> dict[str, str]:
    state = _state(workflow)
    model = _safe_label(_text(state, "device_model"), fallback="设备")
    step = _safe_step(workflow.active_step or _text(state, "current_step"))
    completed_steps = state.get("completed_steps")
    completed_count = len(completed_steps) if isinstance(completed_steps, list) else 0
    if step:
        progress = f"已完成 {completed_count} 个主步骤，当前停留在 {step}。"
        next_step = f"读取 {step} 的设备指导资料并继续。"
    else:
        progress = "开箱指导已经开始，当前步骤待确认。"
        next_step = "先确认设备型号和当前开箱步骤。"
    return {
        "name": f"{model} 开箱指导",
        "progress": progress,
        "next_step": _with_skill_reload(next_step, required_skill="device-guidance", skill_loaded=skill_loaded),
    }


def _with_skill_reload(next_step: str, *, required_skill: str, skill_loaded: bool) -> str:
    if skill_loaded:
        return next_step
    return f"先调用 load_service_skill 加载 {required_skill}；然后{next_step}"


def _state(workflow: AgentWorkflowState) -> dict[str, Any]:
    return workflow.state if isinstance(workflow.state, dict) else {}


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    return str(item).strip()[:300] if item not in (None, "") else ""


def _safe_label(value: str, *, fallback: str) -> str:
    normalized = "".join(character for character in value if character.isalnum() or character in {"-", "_", " "}).strip()
    return normalized[:40] or fallback


def _safe_step(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.startswith("guide."):
        return ""
    suffix = normalized.removeprefix("guide.")
    return normalized if suffix and all(character.isalnum() or character in {"-", "_"} for character in suffix) else ""
