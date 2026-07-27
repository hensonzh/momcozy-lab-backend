from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from app.modules.plans.pregnancy_plan_todos import normalize_pregnancy_todo_periods


PREGNANCY_PLAN_INTAKE_FORM_ID = "birth_journey_basic_info_intake"
PREGNANCY_PLAN_WORKFLOW_TYPE = "pregnancy_plan"
PREGNANCY_PLAN_WORKFLOW_SCHEMA_VERSION = "v3"
PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS = 3
PREGNANCY_PLAN_CHECKUP_DONE_QUESTION = (
    "你目前有没有做过产检？做过的话我再请你上传能找到的记录；还没做过或不确定也可以直接说。"
)
PREGNANCY_PLAN_CHECKUP_UPLOAD_QUESTION = (
    "请上传目前能找到的产检记录，我会把关键复查和待确认项纳入计划；"
    "如果暂时没有或不方便上传，也可以直接跳过。"
)
PREGNANCY_PLAN_FINAL_QUESTION = "还有其他需要补充的信息吗？如果没有，我就基于目前的信息开始为你制定孕期计划啦。"
PREGNANCY_PLAN_URGENT_RESPONSE = (
    "你填写的信息里出现了需要优先线下确认的急症信号。请先停止制定计划，立即联系产科医生、医院产房或急诊；"
    "如果症状严重、正在加重或无法及时联系，请呼叫当地急救服务。"
)


class PregnancyPlanPhase(StrEnum):
    """Durable, user-visible pregnancy-plan intake phases."""

    COLLECTING_INTAKE = "collecting_intake"
    PERSONALIZED_FOLLOWUP = "personalized_followup"
    CHECKUP_DONE_QUESTION = "checkup_done_question"
    CHECKUP_RECORDS_UPLOAD = "checkup_records_upload"
    FINAL_PLAN_CONFIRMATION = "final_plan_confirmation"
    READY_TO_GENERATE = "ready_to_generate"


PREGNANCY_PLAN_INTAKE_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "id": "current_week",
        "label": "当前孕周或预产期",
        "type": "text",
        "required": True,
        "placeholder": "例如：28周、28+3，或 2026-09-12",
    },
    {
        "id": "ivf",
        "label": "是否 IVF（体外受精）",
        "type": "select",
        "required": True,
        "options": ["是", "否", "不确定/暂不说"],
    },
    {
        "id": "fetus_count",
        "label": "单胎/双胎",
        "type": "select",
        "required": True,
        "options": ["单胎", "双胎", "多胎", "不确定/暂不说"],
    },
    {
        "id": "age",
        "label": "年龄",
        "type": "number",
        "required": True,
        "placeholder": "例如：32",
    },
    {
        "id": "first_birth",
        "label": "是否第一胎",
        "type": "select",
        "required": True,
        "options": ["是", "否", "不确定/暂不说"],
    },
    {
        "id": "prior_birth_history",
        "label": "既往孕产情况",
        "type": "textarea",
        "required": False,
        "placeholder": "如：早产、流产、妊娠糖尿病/高血压、产后出血等；没有可写无",
    },
    {
        "id": "birth_path",
        "label": "计划分娩方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "还没确定"],
    },
    {
        "id": "city_or_country",
        "label": "所在城市/国家",
        "type": "text",
        "required": False,
        "placeholder": "例如：深圳 / 美国加州",
    },
    {
        "id": "birth_hospital",
        "label": "建档/生产医院",
        "type": "text",
        "required": False,
        "placeholder": "如果还没建档，可以写“还没确定”",
    },
    {
        "id": "medical_notes",
        "label": "基础疾病或长期用药",
        "type": "textarea",
        "required": False,
        "placeholder": "如：高血压、糖尿病、甲状腺、肾病、自免、心脏病、哮喘等；没有可写无",
    },
    {
        "id": "doctor_notes",
        "label": "医生特殊提醒",
        "type": "textarea",
        "required": False,
        "placeholder": "如：胎盘、胎儿生长、羊水、宫颈、血压血糖、复查等提示；没有可写无",
    },
)

PREGNANCY_PLAN_INTAKE_FIELD_IDS = tuple(str(field["id"]) for field in PREGNANCY_PLAN_INTAKE_FIELDS)
PREGNANCY_PLAN_REQUIRED_FIELD_IDS = tuple(str(field["id"]) for field in PREGNANCY_PLAN_INTAKE_FIELDS if field.get("required") is True)
_SELECT_OPTIONS_BY_FIELD = {
    str(field["id"]): frozenset(str(option) for option in field.get("options", []))
    for field in PREGNANCY_PLAN_INTAKE_FIELDS
    if field.get("options")
}

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "无",
        "没有",
        "none",
        "n/a",
        "不确定",
        "还不确定",
        "暂不说",
        "不确定/暂不说",
        "还没确定",
        "无异常",
        "暂无异常",
        "一切正常",
        "目前一切正常",
        "无特殊情况",
        "没有特殊情况",
        "无基础疾病",
        "没有基础疾病",
        "无长期用药",
        "没有长期用药",
        "无基础疾病或长期用药",
        "没有基础疾病或长期用药",
        "无特殊提醒",
        "没有特殊提醒",
        "医生无特殊提醒",
        "医生没有特殊提醒",
        "无异常孕产史",
        "没有异常孕产史",
        "既往孕产史无异常",
        "既往孕产无异常",
    }
)
_URGENT_PREGNANCY_SIGNAL_PHRASES: dict[str, tuple[str, ...]] = {
    "reduced_fetal_movement": ("胎动明显减少", "胎动突然减少", "感觉不到胎动", "没有胎动"),
    "rupture_of_membranes": ("破水", "羊水破了", "羊水流出"),
    "heavy_bleeding": ("大量出血", "大出血", "阴道出血"),
    "severe_pain": ("剧烈腹痛", "严重腹痛", "严重疼痛"),
    "chest_pain": ("胸痛",),
    "breathing_difficulty": ("呼吸困难", "喘不上气"),
    "fainting": ("晕厥", "晕倒"),
    "severe_headache_with_vision_change": ("严重头痛伴视物异常", "剧烈头痛伴视物异常"),
}
_CONDITIONAL_URGENT_SIGNAL_CUES = ("如果", "若", "一旦")
_NONCURRENT_URGENT_SIGNAL_CUES = ("上次", "既往", "之前", "曾经", "没有", "无", "未", "否认")
_CURRENT_URGENT_SIGNAL_CUES = ("现在", "目前", "刚刚", "今天", "此刻")
_PREGNANCY_PLAN_GENERATION_TEXT_MAX_LENGTH = 2000
_PREGNANCY_PLAN_FOLLOWUP_ANSWER_MAX_LENGTH = 240
_PREGNANCY_PLAN_SCOPE_VALUES = frozenset({"full", "prenatal_only", "short_range"})
_PREGNANCY_PLAN_CHECKUP_STATUSES = frozenset(
    {
        "已上传产检记录",
        "暂不上传",
        "还没做过产检",
        "暂不确定是否做过产检",
    }
)
_PREGNANCY_PLAN_FOLLOWUP_COPY: dict[str, tuple[str, str]] = {
    "doctor_special_notes_followup": (
        "医生特殊提醒",
        "把医生已经提出的复查或观察要求落实到近期日程，并确认异常联系路径。",
    ),
    "prior_c_section_birth_path_detail": (
        "既往剖宫产与本次分娩评估",
        "把既往剖宫产原因、本次评估节点和入院准备带到下一次产检确认。",
    ),
    "prior_preterm_monitoring_detail": (
        "既往早产与本次监测",
        "和产科确认宫颈、宫缩及早产信号的复查节奏和提前联系路径。",
    ),
    "chronic_medical_condition_coordination": (
        "基础疾病或长期用药协同",
        "和产科及相关专科确认用药、复查时间与异常指标联系路径。",
    ),
    "age_35_plus_multiple_monitoring": (
        "高龄与多胎监测重点",
        "确认血压血糖、胎儿生长差异、宫颈与早产信号的个性化监测安排。",
    ),
    "multiple_pregnancy_monitoring": (
        "多胎监测重点",
        "确认多胎类型对应的复查节奏、胎儿生长观察与异常联系路径。",
    ),
    "prior_birth_history_detail": (
        "既往分娩与恢复经历",
        "把仍适用的既往经验和这次需要提前补足的支持带入近期准备。",
    ),
    "age_35_plus_checkup_detail": (
        "高龄孕产复查重点",
        "确认筛查、血压血糖、胎儿生长及其他已被提醒项目的复查节奏。",
    ),
    "ivf_week_confirmation": (
        "IVF 孕周与用药复核",
        "优先对齐医生确认的孕周、移植日期口径、当前用药与复查节点。",
    ),
    "planned_c_section_detail": (
        "计划剖宫产准备",
        "提前确认手术评估、术前检查、大致时间、入院要求与恢复支持。",
    ),
}


def build_pregnancy_plan_intake_form(*, default_values: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = {key: value for key, value in (default_values or {}).items() if key in PREGNANCY_PLAN_INTAKE_FIELD_IDS and _has_value(value)}
    fields = deepcopy(list(PREGNANCY_PLAN_INTAKE_FIELDS))
    for field in fields:
        field_id = str(field["id"])
        if field_id in defaults:
            field["default_value"] = defaults[field_id]
    return {
        "id": PREGNANCY_PLAN_INTAKE_FORM_ID,
        "title": "孕周与基本情况",
        "description": "先填写几项基础信息，后面我会按你的孕周、身体情况和准备状态来整理更贴合你的孕期计划。",
        "submit_label": "提交",
        "fields": fields,
        "default_values": defaults,
    }


def collecting_intake_snapshot(*, form_artifact_id: str) -> dict[str, str]:
    return {
        "phase": PregnancyPlanPhase.COLLECTING_INTAKE.value,
        "source_form_artifact_id": str(form_artifact_id).strip(),
        "form_id": PREGNANCY_PLAN_INTAKE_FORM_ID,
    }


def initialize_pregnancy_plan_workflow(
    values: dict[str, Any],
    *,
    form_artifact_id: str,
    form_submission_id: str,
    analysis_run_id: str,
) -> dict[str, Any]:
    plan_context = normalize_pregnancy_plan_intake(values)
    analysis = analyze_pregnancy_plan_intake(values)
    followup_topics = _pregnancy_plan_followup_topics(plan_context)
    phase = (
        PregnancyPlanPhase.PERSONALIZED_FOLLOWUP
        if followup_topics
        else _phase_after_personalized_followups(analysis)
    )
    workflow: dict[str, Any] = {
        "phase": phase.value,
        "source_form_artifact_id": str(form_artifact_id).strip(),
        "source_form_submission_id": str(form_submission_id).strip(),
        "form_id": PREGNANCY_PLAN_INTAKE_FORM_ID,
        "analysis_run_id": str(analysis_run_id).strip(),
        "plan_context": plan_context,
        "analysis": analysis,
        "followup_topics": followup_topics,
        "personalized_followup_records": [],
    }
    return _with_current_visible_question(workflow)


def pregnancy_plan_current_followup(workflow: dict[str, Any]) -> dict[str, Any] | None:
    if str(workflow.get("phase") or "") != PregnancyPlanPhase.PERSONALIZED_FOLLOWUP.value:
        return None
    records = _followup_records(workflow)
    if len(records) >= PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS:
        return None
    answered = {str(record.get("topic") or "").strip() for record in records}
    topics = workflow.get("followup_topics")
    if not isinstance(topics, list):
        return None
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id") or "").strip()
        if topic_id and topic_id not in answered:
            return deepcopy(topic)
    return None


def pregnancy_plan_current_step(
    workflow: dict[str, Any],
    *,
    checkup_attachment_count: int = 0,
) -> dict[str, Any]:
    """Return the bounded, user-visible command contract for the current step."""

    if workflow.get("paused") is True:
        return {
            "id": "workflow_paused",
            "phase": str(workflow.get("resume_phase") or workflow.get("phase") or ""),
            "kind": "paused",
            "question": "孕期计划已暂停，随时可以从这里继续。",
            "allow_free_text": False,
            "options": [{"id": "resume", "label": "继续孕期计划"}],
        }

    phase = str(workflow.get("phase") or "")
    question = str(workflow.get("visible_question") or "").strip()
    if phase == PregnancyPlanPhase.COLLECTING_INTAKE.value:
        return _workflow_step(
            step_id="basic_intake",
            phase=phase,
            kind="form",
            question=question,
            allow_free_text=False,
        )
    if phase == PregnancyPlanPhase.PERSONALIZED_FOLLOWUP.value:
        current = pregnancy_plan_current_followup(workflow) or {}
        topic_id = str(current.get("id") or "").strip()
        labels = current.get("reply_options")
        options = [
            {
                "id": f"followup:{topic_id}:option:{index}",
                "label": str(label).strip(),
            }
            for index, label in enumerate(labels if isinstance(labels, (list, tuple)) else ())
            if str(label).strip()
        ]
        options.append({"id": "finish_personalized_followups", "label": "跳过剩余问题"})
        records = _followup_records(workflow)
        return _workflow_step(
            step_id=f"followup:{topic_id}",
            phase=phase,
            kind="choice_or_text",
            question=str(current.get("question") or question).strip(),
            allow_free_text=True,
            options=options,
            progress={
                "completed": len(records),
                "total": min(PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS, len(_followup_topics(workflow))),
            },
        )
    if phase == PregnancyPlanPhase.CHECKUP_DONE_QUESTION.value:
        return _workflow_step(
            step_id="checkup_done",
            phase=phase,
            kind="single_choice",
            question=question or PREGNANCY_PLAN_CHECKUP_DONE_QUESTION,
            allow_free_text=False,
            options=[
                {"id": "confirm_checkup_done", "label": "做过产检"},
                {"id": "confirm_no_checkup_yet", "label": "还没做过"},
                {"id": "confirm_checkup_unknown", "label": "不确定"},
            ],
        )
    if phase == PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value:
        checkup_options: list[dict[str, str]] = []
        if max(0, int(checkup_attachment_count)) > 0:
            checkup_options.append({"id": "mark_checkup_records_uploaded", "label": "使用已上传记录"})
        checkup_options.append({"id": "skip_checkup_records", "label": "暂时跳过"})
        return _workflow_step(
            step_id="checkup_records",
            phase=phase,
            kind="upload_or_choice",
            question=question or PREGNANCY_PLAN_CHECKUP_UPLOAD_QUESTION,
            allow_free_text=False,
            options=checkup_options,
        )
    if phase == PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value:
        return _workflow_step(
            step_id="final_confirmation",
            phase=phase,
            kind="choice_or_text",
            question=question or PREGNANCY_PLAN_FINAL_QUESTION,
            allow_free_text=True,
            options=[
                {"id": "confirm_ready_to_generate", "label": "没有了，开始制定"},
                {"id": "submit_final_additional_info", "label": "我还有补充"},
            ],
        )
    if phase == PregnancyPlanPhase.READY_TO_GENERATE.value:
        return _workflow_step(
            step_id="generate_plan",
            phase=phase,
            kind="action",
            question="",
            allow_free_text=False,
            options=[{"id": "generate_plan", "label": "生成孕期计划"}],
        )
    return _workflow_step(
        step_id=phase or "unknown",
        phase=phase,
        kind="unknown",
        question=question,
        allow_free_text=False,
    )


def pregnancy_plan_workflow_context(
    workflow: dict[str, Any],
    *,
    checkup_attachment_count: int = 0,
) -> dict[str, Any]:
    """Bounded context shared by tool output, model projection, and App cards."""

    records = _followup_records(workflow)
    editable_steps: list[dict[str, str]] = []
    if str(workflow.get("source_form_submission_id") or "").strip():
        editable_steps.append({"id": "basic_intake", "label": "基础信息", "answer": "已提交"})
    editable_steps.extend(
        {
            "id": f"followup:{str(record.get('topic') or '').strip()}",
            "label": f"个性化问题 {index}",
            "answer": str(record.get("answer") or "").strip()[:240],
        }
        for index, record in enumerate(records, start=1)
        if str(record.get("topic") or "").strip()
    )
    if workflow.get("checkup_done_confirmed") is True or str(workflow.get("checkup_status") or "").strip():
        editable_steps.append(
            {
                "id": "checkup_done",
                "label": "是否做过产检",
                "answer": (
                    "做过产检"
                    if workflow.get("checkup_done_confirmed") is True
                    else str(workflow.get("checkup_status") or "").strip()[:240]
                ),
            }
        )
    checkup_status = str(workflow.get("checkup_status") or "").strip()
    if workflow.get("checkup_records_uploaded") is True or checkup_status == "暂不上传":
        editable_steps.append(
            {
                "id": "checkup_records",
                "label": "产检记录",
                "answer": "已上传" if workflow.get("checkup_records_uploaded") is True else "暂不上传",
            }
        )
    if workflow.get("final_plan_confirmed") is True:
        plan_context = _dict(workflow, "plan_context")
        editable_steps.append(
            {
                "id": "final_confirmation",
                "label": "最后补充",
                "answer": str(plan_context.get("final_additional_info") or "没有其他补充").strip()[:240],
            }
        )

    current_step = pregnancy_plan_current_step(
        workflow,
        checkup_attachment_count=checkup_attachment_count,
    )
    phase = str(workflow.get("phase") or "")
    return {
        "schema_version": "pregnancy_plan_workflow_context.v1",
        "workflow_type": PREGNANCY_PLAN_WORKFLOW_TYPE,
        "status": "paused" if workflow.get("paused") is True else "active",
        "phase": phase,
        "current_step": current_step,
        "editable_steps": editable_steps[-12:],
        "progress": {
            "followups_completed": len(records),
            "followups_max": PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS,
            "ready_to_generate": phase == PregnancyPlanPhase.READY_TO_GENERATE.value,
        },
        "allowed_commands": _allowed_workflow_commands(workflow),
    }


def resolve_pregnancy_plan_answer(
    workflow: dict[str, Any],
    *,
    choice_id: str,
    free_text: str,
    checkup_attachment_count: int = 0,
) -> tuple[str, dict[str, str]]:
    """Validate a displayed choice and translate it to one legacy transition."""

    step = pregnancy_plan_current_step(
        workflow,
        checkup_attachment_count=checkup_attachment_count,
    )
    normalized_choice = str(choice_id or "").strip()
    normalized_text = str(free_text or "").strip()
    phase = str(workflow.get("phase") or "")
    if workflow.get("paused") is True:
        raise ValueError("pregnancy_plan_workflow_is_paused")
    if phase == PregnancyPlanPhase.PERSONALIZED_FOLLOWUP.value:
        if normalized_choice == "finish_personalized_followups":
            return "finish_personalized_followups", {}
        options = {
            str(option.get("id") or ""): str(option.get("label") or "").strip()
            for option in step.get("options", [])
            if isinstance(option, dict)
        }
        if normalized_choice and normalized_choice not in options:
            raise ValueError("invalid_pregnancy_plan_choice")
        answer = normalized_text or options.get(normalized_choice, "")
        if not answer:
            raise ValueError("missing_pregnancy_plan_followup_answer")
        return "submit_personalized_followup", {"answer": answer[:2000]}

    option_ids = {
        str(option.get("id") or "")
        for option in step.get("options", [])
        if isinstance(option, dict)
    }
    if not normalized_choice or normalized_choice not in option_ids:
        raise ValueError("invalid_pregnancy_plan_choice")
    if phase == PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value:
        if normalized_choice == "submit_final_additional_info":
            if not normalized_text:
                raise ValueError("missing_pregnancy_plan_final_additional_info")
            return normalized_choice, {"additional_info": normalized_text[:2000]}
        return normalized_choice, {}
    if phase in {
        PregnancyPlanPhase.CHECKUP_DONE_QUESTION.value,
        PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value,
    }:
        return normalized_choice, {}
    raise ValueError("pregnancy_plan_workflow_action_not_allowed")


def pause_pregnancy_plan_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(workflow)
    phase = str(updated.get("phase") or "")
    if not phase or phase == PregnancyPlanPhase.READY_TO_GENERATE.value:
        raise ValueError("pregnancy_plan_workflow_cannot_pause")
    if updated.get("paused") is True:
        return updated
    updated["paused"] = True
    updated["resume_phase"] = phase
    return updated


def resume_pregnancy_plan_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(workflow)
    if updated.get("paused") is not True and updated.get("interrupted_by_safety_signal") is not True:
        return _with_current_visible_question(updated)
    resume_phase = str(updated.pop("resume_phase", "") or updated.get("phase") or "")
    updated.pop("paused", None)
    updated.pop("interrupted_by_safety_signal", None)
    updated["phase"] = resume_phase
    return _with_current_visible_question(updated)


def revise_pregnancy_plan_workflow(
    workflow: dict[str, Any],
    *,
    step_id: str,
    choice_id: str,
    answer: str,
    checkup_attachment_count: int = 0,
) -> dict[str, Any]:
    """Append a revision and rebuild the dependent tail without rewriting history."""

    if str(workflow.get("consumed_by_action_id") or "").strip():
        raise ValueError("pregnancy_plan_completed_requires_plan_revision")
    updated = resume_pregnancy_plan_workflow(workflow)
    normalized_step_id = str(step_id or "").strip()
    normalized_choice = str(choice_id or "").strip()
    normalized_answer = str(answer or "").strip()
    previous_answer = ""
    invalidated = ["checkup_done", "checkup_records", "final_confirmation", "generate_plan"]

    if normalized_step_id.startswith("followup:"):
        topic_id = normalized_step_id.partition(":")[2]
        records = _followup_records(updated)
        target = next((record for record in records if str(record.get("topic") or "") == topic_id), None)
        if target is None:
            raise ValueError("pregnancy_plan_revision_step_not_found")
        previous_answer = str(target.get("answer") or "")
        revised_answer = normalized_answer or _historical_followup_option_label(
            updated,
            topic_id=topic_id,
            choice_id=normalized_choice,
        )
        if not revised_answer:
            raise ValueError("missing_pregnancy_plan_followup_answer")
        target["answer"] = revised_answer[:2000]
        updated["personalized_followup_records"] = records
        plan_context = _dict(updated, "plan_context")
        plan_context["personalized_followup_records"] = deepcopy(records)
        plan_context["personalized_facts"] = "；".join(
            f"{record['topic']} / {record['answer']}"
            for record in records
            if record.get("topic") and record.get("answer")
        )
        updated["plan_context"] = plan_context
        _invalidate_pregnancy_plan_tail(updated)
        updated["phase"] = _phase_after_personalized_followups(_dict(updated, "analysis")).value
    elif normalized_step_id == "checkup_done":
        if normalized_choice not in {
            "confirm_checkup_done",
            "confirm_no_checkup_yet",
            "confirm_checkup_unknown",
        }:
            raise ValueError("invalid_pregnancy_plan_choice")
        previous_answer = str(updated.get("checkup_status") or updated.get("checkup_done_confirmed") or "")
        _invalidate_pregnancy_plan_tail(updated)
        plan_context = _dict(updated, "plan_context")
        if normalized_choice == "confirm_checkup_done":
            updated["checkup_done_confirmed"] = True
            updated["phase"] = PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value
        else:
            status = "还没做过产检" if normalized_choice == "confirm_no_checkup_yet" else "暂不确定是否做过产检"
            updated["checkup_status"] = status
            plan_context["checkup_status"] = status
            updated["phase"] = PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value
        updated["plan_context"] = plan_context
        invalidated = ["checkup_records", "final_confirmation", "generate_plan"]
    elif normalized_step_id == "checkup_records":
        if normalized_choice not in {"mark_checkup_records_uploaded", "skip_checkup_records"}:
            raise ValueError("invalid_pregnancy_plan_choice")
        if normalized_choice == "mark_checkup_records_uploaded" and max(0, int(checkup_attachment_count)) < 1:
            raise ValueError("pregnancy_plan_checkup_attachment_required")
        previous_answer = str(updated.get("checkup_status") or "")
        plan_context = _dict(updated, "plan_context")
        if normalized_choice == "mark_checkup_records_uploaded":
            updated["checkup_records_uploaded"] = True
            updated["checkup_status"] = "已上传产检记录"
            plan_context["checkup_records_uploaded"] = "是"
            plan_context["checkup_status"] = "已上传产检记录"
        else:
            updated.pop("checkup_records_uploaded", None)
            updated["checkup_status"] = "暂不上传"
            plan_context.pop("checkup_records_uploaded", None)
            plan_context["checkup_status"] = "暂不上传"
        updated["plan_context"] = plan_context
        updated.pop("final_plan_confirmed", None)
        updated["phase"] = PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value
        invalidated = ["final_confirmation", "generate_plan"]
    elif normalized_step_id == "final_confirmation":
        if normalized_choice not in {"confirm_ready_to_generate", "submit_final_additional_info"}:
            raise ValueError("invalid_pregnancy_plan_choice")
        plan_context = _dict(updated, "plan_context")
        previous_answer = str(plan_context.get("final_additional_info") or "")
        if normalized_choice == "submit_final_additional_info":
            if not normalized_answer:
                raise ValueError("missing_pregnancy_plan_final_additional_info")
            plan_context["final_additional_info"] = normalized_answer[:2000]
        else:
            plan_context.pop("final_additional_info", None)
        updated["plan_context"] = plan_context
        updated["final_plan_confirmed"] = True
        updated["phase"] = PregnancyPlanPhase.READY_TO_GENERATE.value
        invalidated = ["generate_plan"]
    else:
        raise ValueError("pregnancy_plan_revision_step_not_found")

    revisions = updated.get("answer_revisions")
    revision_items = [deepcopy(item) for item in revisions if isinstance(item, dict)] if isinstance(revisions, list) else []
    revision_items.append(
        {
            "revision": len(revision_items) + 1,
            "step_id": normalized_step_id,
            "previous_answer": previous_answer,
            "answer": normalized_answer or _revision_answer_label(updated, normalized_step_id, normalized_choice),
            "choice_id": normalized_choice,
            "invalidated_step_ids": invalidated,
        }
    )
    updated["answer_revisions"] = revision_items[-20:]
    return _with_current_visible_question(updated)


def advance_pregnancy_plan_workflow(
    workflow: dict[str, Any],
    *,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = deepcopy(workflow)
    normalized_action = str(action or "").strip()
    values = payload or {}
    phase = str(updated.get("phase") or "")

    if phase == PregnancyPlanPhase.PERSONALIZED_FOLLOWUP.value:
        if normalized_action == "finish_personalized_followups":
            updated["personalized_followup_done"] = True
            summary = str(values.get("summary") or "").strip()
            if summary:
                updated["personalized_followup_summary"] = summary[:2000]
            updated["phase"] = _phase_after_personalized_followups(_dict(updated, "analysis")).value
            return _with_current_visible_question(updated)
        if normalized_action != "submit_personalized_followup":
            raise ValueError("invalid_pregnancy_plan_followup_action")
        current = pregnancy_plan_current_followup(updated)
        if current is None:
            raise ValueError("pregnancy_plan_followup_not_available")
        topic_id = str(values.get("topic") or values.get("followup_id") or "").strip()
        if topic_id != str(current.get("id") or ""):
            raise ValueError("unexpected_pregnancy_plan_followup_topic")
        answer = str(values.get("answer") or "").strip()
        if not answer:
            raise ValueError("missing_pregnancy_plan_followup_answer")
        records = _followup_records(updated)
        records.append(
            {
                "topic": topic_id,
                "question": str(current.get("question") or "").strip()[:2000],
                "answer": answer[:2000],
                "plan_impact": str(current.get("plan_impact") or "").strip()[:2000],
            }
        )
        updated["personalized_followup_records"] = records
        plan_context = _dict(updated, "plan_context")
        plan_context["personalized_followup_records"] = deepcopy(records)
        plan_context["personalized_facts"] = "；".join(
            f"{record['topic']} / {record['answer']}" for record in records if record.get("topic") and record.get("answer")
        )
        updated["plan_context"] = plan_context
        if len(records) >= PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS or pregnancy_plan_current_followup(updated) is None:
            updated["phase"] = _phase_after_personalized_followups(_dict(updated, "analysis")).value
        return _with_current_visible_question(updated)

    if phase == PregnancyPlanPhase.CHECKUP_DONE_QUESTION.value:
        plan_context = _dict(updated, "plan_context")
        if normalized_action == "confirm_checkup_done":
            updated["checkup_done_confirmed"] = True
            updated["phase"] = PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value
        elif normalized_action in {"confirm_no_checkup_yet", "confirm_checkup_unknown"}:
            checkup_status = "还没做过产检" if normalized_action == "confirm_no_checkup_yet" else "暂不确定是否做过产检"
            updated["checkup_status"] = checkup_status
            plan_context["checkup_status"] = checkup_status
            updated["phase"] = PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value
        else:
            raise ValueError("invalid_pregnancy_plan_checkup_action")
        updated["plan_context"] = plan_context
        return _with_current_visible_question(updated)

    if phase == PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value:
        plan_context = _dict(updated, "plan_context")
        if normalized_action == "mark_checkup_records_uploaded":
            updated["checkup_records_uploaded"] = True
            plan_context["checkup_records_uploaded"] = "是"
            plan_context["checkup_status"] = "已上传产检记录"
        elif normalized_action == "skip_checkup_records":
            updated["checkup_status"] = "暂不上传"
            plan_context["checkup_status"] = "暂不上传"
        else:
            raise ValueError("invalid_pregnancy_plan_checkup_records_action")
        updated["plan_context"] = plan_context
        updated["phase"] = PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value
        return _with_current_visible_question(updated)

    if phase == PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value:
        if normalized_action not in {"confirm_ready_to_generate", "submit_final_additional_info"}:
            raise ValueError("invalid_pregnancy_plan_final_confirmation_action")
        plan_context = _dict(updated, "plan_context")
        if normalized_action == "submit_final_additional_info":
            additional_info = str(values.get("additional_info") or values.get("final_additional_info") or "").strip()
            if not additional_info:
                raise ValueError("missing_pregnancy_plan_final_additional_info")
            plan_context["final_additional_info"] = additional_info[:2000]
        updated["plan_context"] = plan_context
        updated["final_plan_confirmed"] = True
        updated["phase"] = PregnancyPlanPhase.READY_TO_GENERATE.value
        return _with_current_visible_question(updated)

    if phase == PregnancyPlanPhase.READY_TO_GENERATE.value and normalized_action in {
        "confirm_ready_to_generate",
        "submit_final_additional_info",
    }:
        return _with_current_visible_question(updated)
    raise ValueError("pregnancy_plan_workflow_action_not_allowed")


def _pregnancy_plan_followup_topics(plan_context: dict[str, Any]) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    age = _age(plan_context.get("age"))
    prior_history = str(plan_context.get("prior_birth_history") or "").strip()
    medical_notes = str(plan_context.get("medical_notes") or "").strip()
    doctor_notes = str(plan_context.get("doctor_notes") or "").strip()
    is_multiple = _is_multiple(plan_context.get("fetus_count"))

    if _meaningful(doctor_notes):
        topics.append(
            _followup_topic(
                "doctor_special_notes_followup",
                "医生已经给了需要优先落实的特殊提醒。",
                "这会直接影响复查时间、观察重点和异常联系路径。",
                "医生有没有说明这项提醒要在什么时候复查？还不清楚也可以。",
                ("我补充复查时间", "还不清楚", "先放进待确认"),
            )
        )
    if _contains_any(prior_history, ("剖", "c-section", "cesarean")):
        topics.append(
            _followup_topic(
                "prior_c_section_birth_path_detail",
                "既往剖宫产经历会影响这次分娩方式评估和孕晚期准备。",
                "计划需要纳入上次剖宫产原因、这次评估节点和入院准备。",
                "上次剖宫产的主要原因是什么？记不清也可以先放进待确认。",
                ("我补充上次原因", "记不清", "先放进待确认"),
            )
        )
    if _contains_any(prior_history, ("早产", "preterm", "premature")):
        topics.append(
            _followup_topic(
                "prior_preterm_monitoring_detail",
                "既往早产经历会让这次更关注宫颈、宫缩和早产信号。",
                "计划会把相关复查、异常联系路径和提前准备适当前置。",
                "上次大约是在孕多少周生产的？记不清也可以先放进待确认。",
                ("我补充孕周", "记不清", "先放进待确认"),
            )
        )
    if _meaningful(medical_notes):
        topics.append(
            _followup_topic(
                "chronic_medical_condition_coordination",
                "基础疾病或长期用药需要和产科复查、相关专科保持一致。",
                "计划会纳入用药安全确认、专科复查和异常指标联系路径。",
                "医生有没有安排下一次用药确认或相关复查？还没确定也可以。",
                ("已经安排", "还没确定", "先放进待确认"),
            )
        )
    if age is not None and age >= 35 and is_multiple:
        topics.append(
            _followup_topic(
                "age_35_plus_multiple_monitoring",
                f"你 {age} 岁且是多胎妊娠，这会同时影响产科管理分层和多胎监测重点。",
                "计划会更早关注血压血糖、胎儿生长差异、宫颈长度、复查频率和早产信号。",
                "最近一次产检，医生有没有特别交代要重点复查哪一项？没有或还没确认都可以。",
                ("有重点复查", "没有特别提醒", "还没确认"),
            )
        )
    elif is_multiple:
        topics.append(
            _followup_topic(
                "multiple_pregnancy_monitoring",
                "多胎妊娠会更关注胎儿生长差异、宫颈情况、复查频率和早产信号。",
                "计划会把多胎类型对应的复查节奏和异常联系路径纳入近期安排。",
                "医生有没有确认过双胎类型（比如单绒或双绒）？还没确认也可以。",
                ("单绒双羊", "双绒双羊", "还没确认"),
            )
        )
    if _is_no(plan_context.get("first_birth")) and not _meaningful(prior_history):
        topics.append(
            _followup_topic(
                "prior_birth_history_detail",
                "既往分娩和恢复经历会影响这次分娩沟通、入院准备和产后支持。",
                "计划会保留仍适用的经验，并把上次出现的问题提前纳入准备。",
                "上次生产或恢复，有没有哪件事是医生特别提醒、或你这次想提前准备的？没有或记不清都可以。",
                ("有，我补充一下", "没有", "记不清"),
            )
        )
    if age is not None and age >= 35 and not is_multiple:
        topics.append(
            _followup_topic(
                "age_35_plus_checkup_detail",
                f"你 {age} 岁，在产科管理上通常会被归入高龄孕产妇范围。",
                "计划会更早关注筛查选择、血压血糖、胎儿生长和复查节奏。",
                "这次产检有没有被医生提醒要特别留意哪项复查？没有或还没确认都可以。",
                ("有，我补充一下", "没有特别提醒", "还没确认"),
            )
        )
    if _is_yes(plan_context.get("ivf")):
        topics.append(
            _followup_topic(
                "ivf_week_confirmation",
                "IVF/辅助生殖会影响孕周和预产期的确认口径，也可能关联用药复查。",
                "计划会优先对齐医生确认的孕周、移植日期口径、用药与复查节点。",
                "你现在还有医生让你继续用的药吗？不记得药名也没关系。",
                ("还在用药", "已经停了", "不记得药名"),
            )
        )
    if "剖" in str(plan_context.get("birth_path") or "") and not _contains_any(prior_history, ("剖", "c-section", "cesarean")):
        topics.append(
            _followup_topic(
                "planned_c_section_detail",
                "计划剖宫产会影响孕晚期沟通、入院时间和术后支持准备。",
                "计划会提前安排手术评估、术前检查、入院要求和恢复支持。",
                "医生有没有和你说过大致的手术时间？还没确定也可以。",
                ("时间已确定", "还没确定", "我再和医生确认"),
            )
        )
    return topics


def _followup_topic(
    topic_id: str,
    observation: str,
    plan_impact: str,
    question: str,
    reply_options: tuple[str, str, str],
) -> dict[str, Any]:
    return {
        "id": topic_id,
        "observation": observation,
        "management_meaning": observation,
        "plan_impact": plan_impact,
        "question": question,
        "reply_options": list(reply_options),
    }


def _phase_after_personalized_followups(analysis: dict[str, Any]) -> PregnancyPlanPhase:
    stage = _dict(analysis, "stage")
    if str(stage.get("id") or "") == "first_trimester":
        return PregnancyPlanPhase.CHECKUP_DONE_QUESTION
    return PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD


def _with_current_visible_question(workflow: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(workflow)
    phase = str(updated.get("phase") or "")
    question = ""
    if phase == PregnancyPlanPhase.PERSONALIZED_FOLLOWUP.value:
        current = pregnancy_plan_current_followup(updated)
        question = str((current or {}).get("question") or "").strip()
    elif phase == PregnancyPlanPhase.CHECKUP_DONE_QUESTION.value:
        question = PREGNANCY_PLAN_CHECKUP_DONE_QUESTION
    elif phase == PregnancyPlanPhase.CHECKUP_RECORDS_UPLOAD.value:
        question = PREGNANCY_PLAN_CHECKUP_UPLOAD_QUESTION
    elif phase == PregnancyPlanPhase.FINAL_PLAN_CONFIRMATION.value:
        question = PREGNANCY_PLAN_FINAL_QUESTION
    if question:
        updated["visible_question"] = question
    else:
        updated.pop("visible_question", None)
    return updated


def _followup_records(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    records = workflow.get("personalized_followup_records")
    return [deepcopy(record) for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def _followup_topics(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    topics = workflow.get("followup_topics")
    return [deepcopy(topic) for topic in topics if isinstance(topic, dict)] if isinstance(topics, list) else []


def _workflow_step(
    *,
    step_id: str,
    phase: str,
    kind: str,
    question: str,
    allow_free_text: bool,
    options: list[dict[str, str]] | None = None,
    progress: dict[str, int] | None = None,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": step_id,
        "phase": phase,
        "kind": kind,
        "question": question,
        "allow_free_text": allow_free_text,
        "options": list(options or []),
    }
    if progress is not None:
        step["progress"] = progress
    return step


def _historical_followup_option_label(
    workflow: dict[str, Any],
    *,
    topic_id: str,
    choice_id: str,
) -> str:
    prefix = f"followup:{topic_id}:option:"
    if not choice_id.startswith(prefix):
        return ""
    try:
        index = int(choice_id[len(prefix) :])
    except ValueError:
        return ""
    topic = next(
        (item for item in _followup_topics(workflow) if str(item.get("id") or "") == topic_id),
        {},
    )
    options = topic.get("reply_options")
    if not isinstance(options, (list, tuple)) or index < 0 or index >= len(options):
        return ""
    return str(options[index] or "").strip()


def _invalidate_pregnancy_plan_tail(workflow: dict[str, Any]) -> None:
    for key in (
        "checkup_done_confirmed",
        "checkup_records_uploaded",
        "checkup_status",
        "final_plan_confirmed",
    ):
        workflow.pop(key, None)
    plan_context = _dict(workflow, "plan_context")
    for key in ("checkup_records_uploaded", "checkup_status", "final_additional_info"):
        plan_context.pop(key, None)
    workflow["plan_context"] = plan_context


def _revision_answer_label(workflow: dict[str, Any], step_id: str, choice_id: str) -> str:
    if step_id.startswith("followup:"):
        return _historical_followup_option_label(
            workflow,
            topic_id=step_id.partition(":")[2],
            choice_id=choice_id,
        )
    labels = {
        "confirm_checkup_done": "做过产检",
        "confirm_no_checkup_yet": "还没做过",
        "confirm_checkup_unknown": "不确定",
        "mark_checkup_records_uploaded": "使用已上传记录",
        "skip_checkup_records": "暂时跳过",
        "confirm_ready_to_generate": "没有其他补充",
        "submit_final_additional_info": "有补充",
    }
    return labels.get(choice_id, "")


def _allowed_workflow_commands(workflow: dict[str, Any]) -> list[str]:
    if str(workflow.get("consumed_by_action_id") or "").strip():
        return []
    if workflow.get("paused") is True:
        return ["resume", "abandon"]
    phase = str(workflow.get("phase") or "")
    commands = ["pause", "edit_answer", "abandon"]
    if phase == PregnancyPlanPhase.COLLECTING_INTAKE.value:
        commands.insert(0, "submit_form")
    elif phase == PregnancyPlanPhase.READY_TO_GENERATE.value:
        commands.insert(0, "generate_plan")
        commands.remove("pause")
    else:
        commands.insert(0, "answer_current")
    return commands


def _dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return deepcopy(value) if isinstance(value, dict) else {}


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = value.lower()
    return bool(normalized) and any(token.lower() in normalized for token in tokens)


def normalize_pregnancy_plan_intake(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field_id in PREGNANCY_PLAN_INTAKE_FIELD_IDS:
        value = values.get(field_id)
        if isinstance(value, str):
            value = value.strip()
        if _has_value(value):
            normalized[field_id] = value
    current_week = normalized.get("current_week")
    if current_week not in (None, ""):
        normalized.setdefault("due_date_or_week", current_week)
    birth_hospital = normalized.get("birth_hospital")
    if birth_hospital not in (None, ""):
        normalized.setdefault("birth_setting", birth_hospital)
    return normalized


def normalize_pregnancy_plan_generation_context(values: dict[str, Any]) -> dict[str, Any]:
    """Allowlist the owner-scoped facts that may shape or persist a plan card.

    Personalized answers are data to display and verify. They never supply plan
    instructions, titles, or reasoning copy; those come from the trusted topic
    catalog below.
    """

    normalized: dict[str, Any] = {}
    intake = normalize_pregnancy_plan_intake(values)
    for key, value in intake.items():
        if isinstance(value, str):
            text = _bounded_visible_text(value, max_length=_PREGNANCY_PLAN_GENERATION_TEXT_MAX_LENGTH)
            if text:
                normalized[key] = text
        else:
            normalized[key] = value

    for key in (
        "due_date_or_week",
        "estimated_due_date",
        "birth_setting",
        "feeding_intention",
        "support_person",
        "final_additional_info",
    ):
        text = _bounded_visible_text(
            values.get(key),
            max_length=_PREGNANCY_PLAN_GENERATION_TEXT_MAX_LENGTH,
        )
        if text:
            normalized[key] = text

    scope = str(values.get("scope") or "").strip()
    if scope in _PREGNANCY_PLAN_SCOPE_VALUES:
        normalized["scope"] = scope

    records: list[dict[str, str]] = []
    seen_topics: set[str] = set()
    raw_records = values.get("personalized_followup_records")
    if isinstance(raw_records, list):
        for raw_record in raw_records:
            if len(records) >= PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS:
                break
            if not isinstance(raw_record, dict):
                continue
            topic = str(raw_record.get("topic") or "").strip()
            copy = _PREGNANCY_PLAN_FOLLOWUP_COPY.get(topic)
            if copy is None or topic in seen_topics:
                continue
            answer = _bounded_visible_text(
                raw_record.get("answer"),
                max_length=_PREGNANCY_PLAN_FOLLOWUP_ANSWER_MAX_LENGTH,
            )
            if not answer:
                continue
            title, plan_impact = copy
            records.append(
                {
                    "topic": topic,
                    "title": title,
                    "answer": answer,
                    "plan_impact": plan_impact,
                }
            )
            seen_topics.add(topic)
    if records:
        normalized["personalized_followup_records"] = records

    checkup_status = str(values.get("checkup_status") or "").strip()
    if checkup_status not in _PREGNANCY_PLAN_CHECKUP_STATUSES and _is_yes(values.get("checkup_records_uploaded")):
        checkup_status = "已上传产检记录"
    if checkup_status in _PREGNANCY_PLAN_CHECKUP_STATUSES:
        normalized["checkup_status"] = checkup_status
        if checkup_status == "已上传产检记录":
            normalized["checkup_records_uploaded"] = "是"
    return normalized


def missing_pregnancy_plan_intake_fields(values: dict[str, Any]) -> list[str]:
    return [field_id for field_id in PREGNANCY_PLAN_REQUIRED_FIELD_IDS if not _has_value(values.get(field_id))]


def invalid_pregnancy_plan_intake_fields(values: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    if _has_value(values.get("age")) and _age(values.get("age")) is None:
        invalid.append("age")
    for field_id, options in _SELECT_OPTIONS_BY_FIELD.items():
        value = str(values.get(field_id) or "").strip()
        if value and value not in options:
            invalid.append(field_id)
    for field_id in PREGNANCY_PLAN_INTAKE_FIELD_IDS:
        raw_value = values.get(field_id)
        if isinstance(raw_value, str) and len(raw_value) > 2000:
            invalid.append(field_id)
    return list(dict.fromkeys(invalid))


def pregnancy_plan_urgent_signal_ids(values: dict[str, Any]) -> list[str]:
    texts = [str(values.get(key) or "").strip() for key in ("medical_notes", "doctor_notes", "additional_info")]
    matches: list[str] = []
    for text in texts:
        if not text:
            continue
        for signal_id, phrases in _URGENT_PREGNANCY_SIGNAL_PHRASES.items():
            if signal_id in matches:
                continue
            if any(_contains_current_urgent_phrase(text, phrase) for phrase in phrases):
                matches.append(signal_id)
    return matches


def analyze_pregnancy_plan_intake(
    values: dict[str, Any],
    *,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    context = normalize_pregnancy_plan_intake(values)
    timing = _first_text(
        context.get("current_week"),
        values.get("due_date_or_week"),
        values.get("estimated_due_date"),
    )
    week = _gestational_week(timing, as_of_date=as_of_date)
    stage = _pregnancy_stage(week)
    focuses: list[dict[str, str]] = [
        _stage_focus(stage=stage, week=week),
    ]

    age = _age(context.get("age"))
    if age is not None and age >= 35:
        focuses.append(
            _focus(
                "advanced_maternal_age",
                "高龄孕产管理",
                f"你填写的年龄是 {age} 岁，产科通常会把这作为管理分层因素。",
                "计划会更早纳入筛查选择、血压血糖、胎儿生长和复查节奏，并以医生安排为准。",
            )
        )
    if _is_yes(context.get("ivf")):
        focuses.append(
            _focus(
                "ivf_pregnancy",
                "IVF 孕周与复查口径",
                "IVF/辅助生殖会影响孕周和预产期的确认口径，也可能关联用药复查。",
                "计划会优先对齐医生确认的孕周、用药与复查节点。",
            )
        )
    if _is_multiple(context.get("fetus_count")):
        focuses.append(
            _focus(
                "multiple_pregnancy",
                "多胎监测与提前准备",
                "多胎妊娠通常需要更关注胎儿生长差异、宫颈情况和早产信号。",
                "计划会把复查节奏、异常联系路径和入院准备适当前置。",
            )
        )
    if _is_yes(context.get("first_birth")):
        focuses.append(
            _focus(
                "first_birth_preparation",
                "第一胎流程准备",
                "第一次经历产检、临产和入院流程时，把关键节点说清楚会更容易执行。",
                "计划会把产检节奏、临产信号、入院流程和支持人分工拆得更具体。",
            )
        )
    elif _is_no(context.get("first_birth")):
        focuses.append(
            _focus(
                "prior_birth_experience",
                "既往分娩经验复盘",
                "这不是第一次分娩，既往经历可能影响这次准备、沟通和恢复支持的重点。",
                "计划会安排一次简短复盘，只沿用这次仍适用的经验，不推断上次的具体情况。",
            )
        )
    if _meaningful(context.get("prior_birth_history")):
        focuses.append(
            _focus(
                "prior_birth_history",
                "既往孕产经历",
                "既往孕产经历会影响这次复查、分娩沟通和产后准备的重点。",
                "计划会保留这部分作为和产科团队确认的依据，不自行下医学结论。",
            )
        )
    if _meaningful(context.get("medical_notes")):
        focuses.append(
            _focus(
                "medical_coordination",
                "基础疾病与长期用药协同",
                "基础疾病或长期用药需要和产科复查、相关专科及用药确认保持一致。",
                "计划会突出用药确认、复查时间和需要联系医生的节点。",
            )
        )
    if _meaningful(context.get("doctor_notes")):
        focuses.append(
            _focus(
                "doctor_followup",
                "医生提醒优先落地",
                "医生已经给出的特殊提醒比通用建议更有优先级。",
                "计划会优先安排对应复查、观察重点和异常联系路径。",
            )
        )
    if "剖" in str(context.get("birth_path") or ""):
        focuses.append(
            _focus(
                "planned_c_section",
                "剖宫产准备",
                "计划剖宫产会影响孕晚期沟通、入院时间和术后支持准备。",
                "计划会提前安排手术沟通、入院要求和恢复支持事项。",
            )
        )

    return {
        "stage": {
            "id": stage,
            "current_week": week,
            "summary": _stage_summary(stage),
        },
        "focuses": focuses,
        "final_question": PREGNANCY_PLAN_FINAL_QUESTION,
    }


def build_pregnancy_plan_card_json(plan_context: dict[str, Any]) -> dict[str, Any]:
    context = normalize_pregnancy_plan_generation_context(plan_context)
    analysis = analyze_pregnancy_plan_intake(context)
    stage = str(analysis["stage"]["id"])
    week = analysis["stage"].get("current_week")
    scope = str(context.get("scope") or "full")
    focus_ids = [str(item["id"]) for item in analysis["focuses"] if isinstance(item, dict) and item.get("id")]
    focus_set = set(focus_ids)
    current_items = _stage_todo_items(stage)

    if focus_set & {"advanced_maternal_age", "ivf_pregnancy", "multiple_pregnancy"}:
        current_items.append(
            _todo(
                "align_personalized_monitoring",
                "和产科确认个性化复查节奏",
                "年龄、IVF 或多胎等管理因素会改变筛查、复查和观察重点。",
                [
                    "确认医生采用的孕周与预产期口径",
                    "把下一次筛查、复查和胎儿生长观察时间记进日程",
                    "确认哪些变化需要提前联系医生或医院",
                ],
            )
        )
    if "prior_birth_history" in focus_set:
        current_items.append(
            _todo(
                "review_prior_pregnancy_history",
                "把既往孕产经历带进这次复查",
                "既往经历会影响这次复查、分娩沟通和产后准备的重点。",
                ["整理一份既往孕产关键时间线", "产检时确认这次需要提前关注的项目", "把确认后的安排更新到计划"],
            )
        )
    if "prior_birth_experience" in focus_set:
        current_items.append(
            _todo(
                "review_prior_birth_experience",
                "复盘上次分娩与恢复经验",
                "既往经验能帮助你保留这次仍适用的做法，也提前补上需要调整的支持。",
                ["写下上次最有帮助的一件事", "标记这次想调整的一件事", "和支持人或产科团队确认可执行范围"],
            )
        )
    if "medical_coordination" in focus_set:
        current_items.append(
            _todo(
                "coordinate_medication_and_specialty_care",
                "确认长期用药和复查安排",
                "基础疾病或长期用药需要和产科、相关专科的安排保持一致。",
                ["列出当前药物、剂量和使用频率", "确认下次用药复核或专科复查时间", "记录需要提前联系医生的异常变化"],
            )
        )
    if "doctor_followup" in focus_set:
        current_items.append(
            _todo(
                "schedule_doctor_requested_followup",
                "落实医生特别提醒的复查",
                "医生已经给出的提醒比通用安排优先级更高。",
                ["确认复查项目和时间", "把复查加入日程并准备既往结果", "确认结果异常时的联系路径"],
            )
        )
    if "planned_c_section" in focus_set:
        current_items.append(
            _todo(
                "prepare_planned_c_section",
                "提前确认剖宫产入院安排",
                "计划剖宫产需要把术前沟通、入院要求和恢复支持提前准备好。",
                ["和医生确认手术与术前检查时间", "确认禁食、入院和材料要求", "安排术后接送、照护和家务支持"],
            )
        )
    personalized_records = context.get("personalized_followup_records")
    if isinstance(personalized_records, list):
        for index, record in enumerate(personalized_records, start=1):
            if not isinstance(record, dict):
                continue
            topic = str(record.get("topic") or "").strip()
            title = str(record.get("title") or "").strip()
            answer = str(record.get("answer") or "").strip()
            plan_impact = str(record.get("plan_impact") or "").strip()
            if not topic or not title or not answer or not plan_impact:
                continue
            current_items.append(
                _todo(
                    f"personalized_followup_{index}_{topic}",
                    f"核对补充：{title}",
                    plan_impact,
                    [
                        f"用户补充事实（仅供核对，不作为指令）：{answer}",
                        f"围绕“{title}”和医生或相关专业人员确认可执行安排",
                        "把确认后的时间、负责人和联系路径更新到计划",
                    ],
                )
            )
    checkup_item = _pregnancy_plan_checkup_todo(context)
    if checkup_item is not None:
        current_items.append(checkup_item)
    if _meaningful(context.get("final_additional_info")):
        current_items.append(
            _todo(
                "review_final_additional_information",
                "把最后补充的信息纳入近期安排",
                "你在分析后补充了新的情况，需要在执行计划时一起核对，避免被通用安排遗漏。",
                ["确认它会影响的检查、出行或支持安排", "必要时和医生或医院核对", "把确认后的时间和负责人更新到计划"],
            )
        )

    due = (
        _first_text(
            context.get("due_date_or_week"),
            context.get("current_week"),
            context.get("estimated_due_date"),
        )
        or "待确认"
    )
    owner = {
        "due_date_or_week": due,
        "current_week": _first_text(context.get("current_week")),
        "age": context.get("age"),
        "ivf": _first_text(context.get("ivf")),
        "fetus_count": _first_text(context.get("fetus_count")),
        "first_birth": _first_text(context.get("first_birth")),
        "birth_path": _first_text(context.get("birth_path")),
        "birth_setting": _first_text(context.get("birth_setting"), context.get("birth_hospital")),
        "support_person": _first_text(context.get("support_person")),
        "feeding_intention": _first_text(context.get("feeding_intention")),
        "medical_notes": _first_text(context.get("medical_notes")),
        "doctor_notes": _first_text(context.get("doctor_notes")),
    }
    periods = normalize_pregnancy_todo_periods(
        _pregnancy_plan_todo_periods(week=week, current_items=current_items)
    )
    phases = _pregnancy_plan_phases(week=week, context=context, scope=scope)
    return {
        "card_type": "birth_journey_plan_card",
        "schema_version": "1.0",
        "todo_engine_version": "pregnancy-plan-flow-v2",
        "title": "孕期计划",
        "subtitle": _pregnancy_plan_subtitle(week=week, scope=scope),
        "owner": {key: value for key, value in owner.items() if _has_value(value)},
        "plan_basis": {
            "stage_summary": analysis["stage"]["summary"],
            "focus_count": len(focus_ids),
            "personalized": len(focus_ids) > 1,
            "additional_information_included": _meaningful(context.get("final_additional_info")),
            "personalized_followup_count": len(personalized_records) if isinstance(personalized_records, list) else 0,
            "checkup_status": _first_text(context.get("checkup_status")),
        },
        "todo_plan": {
            "cadence": _pregnancy_plan_cadence(week)[0],
            "cadence_label": _pregnancy_plan_cadence(week)[1],
            "cadence_reason": _pregnancy_plan_cadence_reason(week),
            "route_summary": _pregnancy_plan_route_summary(week),
            "periods": periods,
        },
        "phases": phases,
        "generation_context": {
            "source": "verified_pregnancy_plan_intake",
            "scope": scope,
            "current_week": week,
            "additional_information_provided": _meaningful(context.get("final_additional_info")),
            "personalized_followup_count": len(personalized_records) if isinstance(personalized_records, list) else 0,
            "checkup_status": _first_text(context.get("checkup_status")),
        },
        "next_action": {"label": "继续整理待产包", "send_text": "帮我整理一份个性化待产包清单"},
        "disclaimer": (
            "这份计划用于准备和沟通，不能替代医生、助产士或医院的具体建议；有破水、出血、胎动明显减少、"
            "规律宫缩加密或明显不适时，请按医院或医生指导处理。"
        ),
    }


def _pregnancy_plan_subtitle(*, week: Any, scope: str) -> str:
    if scope == "prenatal_only":
        return f"从孕{week}周到生产前的阶段路线图" if isinstance(week, int) else "从现在到生产前的阶段路线图"
    if scope == "short_range":
        return f"从孕{week}周开始的近期准备节奏" if isinstance(week, int) else "从现在开始的近期准备节奏"
    return f"从孕{week}周到产后 42 天的阶段路线图" if isinstance(week, int) else "从现在到产后 42 天的阶段路线图"


def _pregnancy_plan_phases(*, week: Any, context: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    phase_ids: list[str] = []
    if isinstance(week, int) and week <= 13:
        phase_ids.append("early_pregnancy")
    if isinstance(week, int) and week <= 27:
        phase_ids.append("mid_pregnancy")
    phase_ids.extend(["late_pregnancy", "labor_recognition", "hospital_birth", "postpartum"])
    if scope == "prenatal_only":
        phase_ids = [phase_id for phase_id in phase_ids if phase_id not in {"hospital_birth", "postpartum"}][:4]
    elif scope == "short_range":
        phase_ids = phase_ids[:2]
    else:
        phase_ids = phase_ids[:6]

    phases: list[dict[str, Any]] = []
    for index, phase_id in enumerate(phase_ids):
        phase = _pregnancy_plan_phase(phase_id=phase_id, context=context)
        phase["status"] = "current" if index == 0 else "upcoming"
        phases.append(phase)
    return phases


def _pregnancy_plan_phase(*, phase_id: str, context: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, tuple[str, str, str, list[str], list[str]]] = {
        "early_pregnancy": (
            "孕早期",
            "孕 1-13 周",
            "确认怀孕情况，顺利完成首次产检，把重要信息准备好。",
            ["确认首次产检或建档时间", "整理检查结果、用药和补充剂信息", "记下想咨询医生的问题"],
            ["现阶段先关注产检和身体变化，不用着急考虑生产和待产准备。"],
        ),
        "mid_pregnancy": (
            "孕中期",
            "孕 14-27 周",
            "关注宝宝发育，跟上产检节奏，并开始规划生产和产后支持。",
            ["准备下次产检想问的问题", "了解生产医院和相关流程", "和家人讨论产后支持安排"],
            ["很多事情不用一次准备完成，先把医院选择和家庭支持安排理顺。"],
        ),
        "late_pregnancy": (
            "孕晚期",
            "孕 28-36 周",
            "逐步落实生产前准备，让临产时更从容。",
            ["确认医院入院和陪产要求", "准备待产包和重要证件", "和家人明确临产时的分工安排"],
            ["距离生产越来越近，提前做好准备会让临产和住院过程更顺利。"],
        ),
        "labor_recognition": (
            "临产阶段",
            "孕 37 周起",
            "了解临产信号，知道什么时候联系医院、什么时候出发。",
            ["保存医院和重要联系人的电话", "熟悉去医院的路线和交通方案", "把证件和住院材料放在容易拿取的位置"],
            ["如果出现破水、大量出血、胎动明显减少或其他异常情况，请及时联系医院。"],
        ),
        "hospital_birth": (
            "住院分娩",
            "入院当天～出院当天",
            "专注分娩和恢复，把重要沟通和记录安排好。",
            ["和医护确认你的重点需求", "记录妈妈和宝宝的重要情况", "出院前确认复诊和护理事项"],
            ["医疗决策以医护团队建议为准，有任何需求或担忧都可以及时沟通。"],
        ),
        "postpartum": (
            "产后恢复",
            "出院后 0～42 天",
            "关注妈妈恢复和宝宝喂养，让家庭逐步适应新的节奏。",
            ["记录喂养、尿布和宝宝情况", "关注身体恢复情况", "安排夜间照护和休息时间"],
            ["如果妈妈或宝宝出现异常情况，请及时联系医生、儿科医生或 IBCLC。"],
        ),
    }
    title, date_range, goal, actions, watchouts = base[phase_id]
    personalized_actions = list(actions)
    if phase_id in {"late_pregnancy", "labor_recognition"} and _is_yes(context.get("first_birth")):
        personalized_actions.append("让支持人也看一遍入院流程和临产信号")
    if phase_id in {"late_pregnancy", "hospital_birth", "postpartum"} and "剖" in str(context.get("birth_path") or ""):
        personalized_actions.append("按医生口径确认剖宫产入院、术前和恢复安排")
    if phase_id in {"hospital_birth", "postpartum"} and any(
        token in str(context.get("feeding_intention") or "") for token in ("母乳", "混合", "泵")
    ):
        personalized_actions.append("尽早确认含乳、涨奶处理和 IBCLC/泌乳顾问支持")
    return {
        "id": phase_id,
        "title": title,
        "date_range": date_range,
        "goal": goal,
        "watchouts": watchouts[:4],
        "actions": personalized_actions[:4],
        "comate_help": ["制定个性化待产清单"] if phase_id == "late_pregnancy" else [],
    }


def _pregnancy_plan_cadence(week: Any) -> tuple[str, str]:
    if isinstance(week, int) and week >= 36:
        return "weekly", "每周计划"
    if isinstance(week, int) and week >= 28:
        return "biweekly", "双周计划"
    return "monthly", "按月计划"


def _pregnancy_plan_cadence_reason(week: Any) -> str:
    if not isinstance(week, int):
        return "补齐孕周后，再按当前阶段选择按月、双周或每周推进。"
    if week >= 36:
        return "36 周后产检和临产信号更密集，适合每周逐项确认。"
    if week >= 28:
        return "进入孕晚期后，产检、胎动观察和入院准备变密，适合按双周推进。"
    return f"孕 {week} 周阶段适合先按 4 周窗口推进，把检查、复查和生活安排分块完成。"


def _pregnancy_plan_route_summary(week: Any) -> str:
    if not isinstance(week, int):
        return "补齐孕周后，会按当前阶段生成从现在到住院生产的路线。"
    if week < 28:
        return f"从孕 {week} 周开始，先按月推进，孕晚期改成双周，36 周后按周收口到住院生产。"
    if week < 36:
        return f"从孕 {week} 周开始，先按双周推进，36 周后按周收口到住院生产。"
    return f"从孕 {week} 周开始，按每周产检和临产信号一路收口到住院生产。"


def _pregnancy_plan_todo_periods(*, week: Any, current_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(week, int):
        return [
            {
                "id": "period_01",
                "title": "补齐孕周后生成清单",
                "week_start": None,
                "week_end": None,
                "granularity": "monthly",
                "display_mode": "expanded",
                "status": "current",
                "subtitle": "先补充孕周或预产期，再把产检窗口、身体变化和生产准备排成可执行事项。",
                "items": current_items,
            },
            _pregnancy_plan_terminal_period(),
        ]

    periods: list[dict[str, Any]] = []
    start_week = max(1, min(40, week))
    while start_week <= 40:
        index = len(periods) + 1
        if start_week >= 36:
            granularity, span = "weekly", 1
        elif start_week >= 28:
            granularity, span = "biweekly", 2
        else:
            granularity, span = "monthly", 4
        end_week = min(40, start_week + span - 1)
        if start_week < 28:
            end_week = min(end_week, 27)
        elif start_week < 36:
            end_week = min(end_week, 35)
        title = f"孕 {start_week} 周" if start_week == end_week else f"孕 {start_week}-{end_week} 周"
        period_stage = _pregnancy_stage(start_week)
        periods.append(
            {
                "id": f"period_{index:02d}",
                "title": title,
                "week_start": start_week,
                "week_end": end_week,
                "granularity": granularity,
                "display_mode": "expanded" if index == 1 else "collapsed",
                "status": "current" if index == 1 else "upcoming",
                "subtitle": "先完成会影响近期检查、沟通和安心感的事项" if index == 1 else _stage_summary(period_stage),
                "items": current_items if index == 1 else _stage_todo_items(period_stage),
            }
        )
        start_week = end_week + 1
    periods.append(_pregnancy_plan_terminal_period())
    return periods


def _pregnancy_plan_terminal_period() -> dict[str, Any]:
    return {
        "id": "period_terminal",
        "title": "临产与住院生产",
        "week_start": None,
        "week_end": None,
        "granularity": "terminal",
        "display_mode": "terminal",
        "status": "terminal",
        "subtitle": "把临产信号、医院入口、证件报告和陪同分工收口。",
        "items": _labor_and_hospital_items(),
    }


def build_pregnancy_plan_result(
    plan_context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    card_json = build_pregnancy_plan_card_json(plan_context)
    generation_context = dict(card_json.get("generation_context") or {})
    generated_at = now or datetime.now(timezone.utc)
    generation_context["created_at"] = generated_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    card_json["generation_context"] = generation_context
    return {
        "tool_name": "plan_mutate",
        "status": "card_created",
        "summary": "孕期计划已生成",
        "card": {
            "card_type": "birth_journey_plan_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
    }


def ensure_pregnancy_plan_final_question(text: str) -> str:
    normalized = str(text or "").strip()
    if normalized.endswith(PREGNANCY_PLAN_FINAL_QUESTION):
        return normalized
    question_prefix = "还有其他需要补充的信息吗？"
    if normalized.endswith(question_prefix):
        return f"{normalized}{PREGNANCY_PLAN_FINAL_QUESTION[len(question_prefix) :]}"
    if not normalized:
        return PREGNANCY_PLAN_FINAL_QUESTION
    return f"{normalized}\n\n{PREGNANCY_PLAN_FINAL_QUESTION}"


def _stage_todo_items(stage: str) -> list[dict[str, Any]]:
    if stage == "first_trimester":
        return [
            _todo(
                "confirm_pregnancy_timing",
                "确认孕周、预产期和建档安排",
                "后续筛查和复查时间都以可靠的孕周口径为基础。",
                ["确认医生采用的孕周和预产期", "确认建档医院与时间", "把下一次产检加入日程"],
            ),
            _todo(
                "schedule_early_screening",
                "安排孕早期筛查时间窗",
                "早期筛查项目有明确时间窗，提前排好更不容易错过。",
                ["确认近期需要做的筛查项目", "预约或记录检查日期", "保存结果并确认下一步"],
            ),
        ]
    if stage == "second_trimester":
        return [
            _todo(
                "schedule_mid_pregnancy_checks",
                "排好孕中期检查时间",
                "系统超声、糖耐等检查集中在孕中期，需要按孕周衔接。",
                ["确认下一项检查及合适孕周", "完成预约并准备注意事项", "结果出来后确认是否需要复查"],
            ),
            _todo(
                "track_checkup_results",
                "把产检结果和复查要求整理在一起",
                "把结果、医生提醒和复查时间连起来，后续更容易执行。",
                ["保存最近一次产检结果", "标记医生要求复查的项目", "把复查日期加入日程"],
            ),
        ]
    if stage == "third_trimester":
        return [
            _todo(
                "confirm_late_pregnancy_checks",
                "确认孕晚期复查节奏",
                "孕晚期复查会逐渐密集，需要把检查、结果和下一次安排连起来。",
                ["确认下一次产检时间和项目", "完成后记录医生提醒", "把后续复查更新到日程"],
            ),
            _todo(
                "know_hospital_signals",
                "确认什么时候需要联系医院",
                "提前确认医院口径，遇到破水、出血、胎动变化或宫缩时更容易行动。",
                ["保存产科或医院联系电话", "确认医院要求关注的入院信号", "和支持人对齐接送与入院路线"],
            ),
            _todo(
                "prepare_hospital_bag",
                "开始整理待产包",
                "先准备证件、妈妈住院用品和宝宝出院用品，不用一次买齐。",
                ["先确认医院会提供什么", "优先准备证件和必需品", "按住院与出院场景分包装好"],
            ),
        ]
    return [
        _todo(
            "confirm_gestational_timing",
            "先确认当前孕周和下一次产检",
            "孕周口径会决定后续检查和准备事项的先后顺序。",
            ["确认当前孕周或预产期", "确认建档或产检医院", "把下一次产检加入日程"],
        ),
        _todo(
            "confirm_support_plan",
            "先确定近期支持安排",
            "有人能协助就医、家务或临时情况处理，会让计划更容易执行。",
            ["确认一位主要支持人", "对齐接送和紧急联系方法", "把暂时无人负责的事项列出来"],
        ),
    ]


def _labor_and_hospital_items() -> list[dict[str, Any]]:
    return [
        _todo(
            "finalize_hospital_route",
            "确认去医院的路线和入院要求",
            "临产时少做临时决定，能把精力留给妈妈和宝宝。",
            ["保存医院入口和产科电话", "确认需要携带的证件材料", "和支持人走一遍接送方案"],
        ),
        _todo(
            "prepare_birth_communication",
            "整理分娩沟通重点",
            "提前写清楚偏好和需要医护先沟通的事项，现场更容易表达。",
            ["列出最重要的 3 项偏好", "和支持人对齐如何协助沟通", "产检时和医生确认可行范围"],
        ),
        _todo(
            "confirm_postpartum_support",
            "确认产后前两周支持安排",
            "接送、家务、夜间照护和复诊支持越明确，产后越不容易手忙脚乱。",
            ["确认谁负责接送和家务", "确认夜间与白天照护分工", "保存产后复诊和求助联系方式"],
        ),
    ]


def _todo(item_id: str, title: str, reason: str, steps: list[str]) -> dict[str, Any]:
    return {
        "id": item_id,
        "item_id": item_id,
        "title": title,
        "reason": reason,
        "why_for_you": reason,
        "steps": steps[:3],
        "priority_type": "essential",
        "priority_label": "重要",
    }


def _pregnancy_plan_checkup_todo(context: dict[str, Any]) -> dict[str, Any] | None:
    status = str(context.get("checkup_status") or "").strip()
    if status == "已上传产检记录":
        return _todo(
            "review_uploaded_checkup_records",
            "核对已上传记录中的复查与待确认项",
            "已上传的记录只作为核对依据，需要把复查时间、待确认结果和联系路径落实到计划。",
            ["逐项核对记录里的复查或待确认提示", "向医生确认不清楚或缺少的结果", "把确认后的日期加入近期日程"],
        )
    if status == "暂不上传":
        return _todo(
            "complete_checkup_record_review_later",
            "之后补齐产检记录或口头核对关键结果",
            "这次暂未上传记录，先保留一个明确的补齐入口，避免重要复查被遗漏。",
            ["方便时上传现有产检记录", "没有文件时可列出最近一次检查和医生提醒", "把确认后的复查日期加入日程"],
        )
    if status == "还没做过产检":
        return _todo(
            "schedule_first_checkup",
            "安排首次产检并确认检查清单",
            "尚未做过产检时，下一步应优先确认线下评估、孕周口径和检查安排。",
            ["联系合适的产科或医院预约首次产检", "询问需要携带的资料和检查准备", "把预约和结果复核时间加入日程"],
        )
    if status == "暂不确定是否做过产检":
        return _todo(
            "confirm_checkup_history",
            "确认既往产检情况与下一步",
            "当前产检情况还不确定，先厘清已有检查和下一次安排，避免重复或遗漏。",
            ["核对医院、应用或纸质记录中的既往检查", "不确定时联系产科确认", "记录下一次检查或补查时间"],
        )
    return None


def _current_period_title(*, stage: str, week: Any) -> str:
    if isinstance(week, int):
        return f"当前阶段｜孕 {week} 周起"
    return {
        "first_trimester": "当前阶段｜孕早期",
        "second_trimester": "当前阶段｜孕中期",
        "third_trimester": "当前阶段｜孕晚期",
        "unknown": "当前阶段",
    }[stage]


def _stage_focus(*, stage: str, week: int | None) -> dict[str, str]:
    focus_id = "late_pregnancy_timing" if stage == "third_trimester" else "pregnancy_stage_timing"
    week_text = f"当前约孕 {week} 周。" if week is not None else "当前孕周或预产期已经记录。"
    return _focus(
        focus_id,
        "当前孕期阶段",
        f"{week_text}{_stage_summary(stage)}",
        "计划会从当前阶段开始安排，不重复已经错过或尚未临近的事项。",
    )


def _stage_summary(stage: str) -> str:
    return {
        "first_trimester": "现阶段重点是确认孕周、建档和早期筛查时间窗。",
        "second_trimester": "现阶段重点是中期筛查、系统超声、糖耐等检查时间窗和日常准备。",
        "third_trimester": "现阶段重点是复查节奏、胎动与入院信号、分娩沟通和待产准备。",
        "unknown": "接下来会先按已确认信息安排通用节点，并把孕周口径列为待确认项。",
    }[stage]


def _pregnancy_stage(week: int | None) -> str:
    if week is None:
        return "unknown"
    if week <= 13:
        return "first_trimester"
    if week <= 27:
        return "second_trimester"
    return "third_trimester"


def _gestational_week(value: Any, *, as_of_date: date | None = None) -> int | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?:孕\s*)?(\d{1,2})(?:\s*\+\s*\d{1,2})?\s*(?:周)?", text)
    if match is not None:
        week = int(match.group(1))
        return week if 1 <= week <= 45 else None
    due_date = _iso_date(text)
    if due_date is None:
        return None
    today = as_of_date or datetime.now(timezone.utc).date()
    gestational_days = 280 - (due_date - today).days
    if not 7 <= gestational_days <= 45 * 7:
        return None
    return gestational_days // 7


def _iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _age(value: Any) -> int | None:
    try:
        age = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return age if 12 <= age <= 70 else None


def _contains_current_urgent_phrase(text: str, phrase: str) -> bool:
    start = text.find(phrase)
    while start >= 0:
        prefix = text[max(0, start - 24) : start]
        clause_prefix = re.split(r"[，,。；;]|但|不过", prefix)[-1]
        if any(cue in clause_prefix for cue in _CONDITIONAL_URGENT_SIGNAL_CUES):
            start = text.find(phrase, start + len(phrase))
            continue
        last_current = max((clause_prefix.rfind(cue) for cue in _CURRENT_URGENT_SIGNAL_CUES), default=-1)
        last_noncurrent = max((clause_prefix.rfind(cue) for cue in _NONCURRENT_URGENT_SIGNAL_CUES), default=-1)
        if last_noncurrent < 0 or last_current > last_noncurrent:
            return True
        start = text.find(phrase, start + len(phrase))
    return False


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"是", "yes", "y", "true", "1"}


def _is_no(value: Any) -> bool:
    return str(value or "").strip().lower() in {"否", "no", "n", "false", "0"}


def _is_multiple(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(token in text for token in ("双", "多胎", "twins", "multiple", "triplet"))


def _meaningful(value: Any) -> bool:
    text = str(value or "").strip().lower().strip("。.!！,，;；")
    return text not in _PLACEHOLDER_VALUES


def _has_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    if value is None:
        return False
    return bool(str(value).strip())


def _focus(focus_id: str, title: str, management_meaning: str, plan_impact: str) -> dict[str, str]:
    return {
        "id": focus_id,
        "title": title,
        "management_meaning": management_meaning,
        "plan_impact": plan_impact,
    }


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _bounded_visible_text(value: Any, *, max_length: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:max_length].strip()
