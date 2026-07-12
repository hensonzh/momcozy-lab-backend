from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any


PREGNANCY_PLAN_INTAKE_FORM_ID = "birth_journey_basic_info_intake"
PREGNANCY_PLAN_WORKFLOW_ARTIFACT_TYPE = "pregnancy_plan_workflow"
PREGNANCY_PLAN_WORKFLOW_SCHEMA_VERSION = "v2"
PREGNANCY_PLAN_FOLLOWUP_MAX_ROUNDS = 3
PREGNANCY_PLAN_CHECKUP_DONE_QUESTION = (
    "你目前有没有做过产检？做过的话我再请你上传能找到的记录；还没做过或不确定也可以直接说。"
)
PREGNANCY_PLAN_CHECKUP_UPLOAD_QUESTION = (
    "请上传目前能找到的产检记录，我会把关键复查和待确认项纳入计划；"
    "如果暂时没有或不方便上传，也可以直接跳过。"
)
PREGNANCY_PLAN_FINAL_QUESTION = "还有其他需要补充的信息吗？如果没有，我就基于目前的信息开始为你制定孕期计划啦。"
PREGNANCY_PLAN_FINAL_QUICK_REPLIES = ("没有了，开始制定", "我想补充一点", "稍等我再看看")
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
                "question": str(values.get("question") or current.get("question") or "").strip()[:2000],
                "answer": answer[:2000],
                "plan_impact": str(values.get("plan_impact") or current.get("plan_impact") or "").strip()[:2000],
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
                "这项提醒具体对应什么复查或观察要求、计划在什么时候完成？如果暂时不清楚，可以说“还不确定”。",
                ("我补充具体安排", "还不确定", "先放进待确认"),
            )
        )
    if _contains_any(prior_history, ("剖", "c-section", "cesarean")):
        topics.append(
            _followup_topic(
                "prior_c_section_birth_path_detail",
                "既往剖宫产经历会影响这次分娩方式评估和孕晚期准备。",
                "计划需要纳入上次剖宫产原因、这次评估节点和入院准备。",
                "上次剖宫产的主要原因是什么，这次目前倾向顺产还是再次剖宫产？不确定也可以先记为待确认。",
                ("我补充上次原因", "还不确定", "先放进待确认"),
            )
        )
    if _contains_any(prior_history, ("早产", "preterm", "premature")):
        topics.append(
            _followup_topic(
                "prior_preterm_monitoring_detail",
                "既往早产经历会让这次更关注宫颈、宫缩和早产信号。",
                "计划会把相关复查、异常联系路径和提前准备适当前置。",
                "上次大约在多少孕周早产，这次有没有已经在复查宫颈或被提醒关注宫缩？暂不清楚也可以。",
                ("我补充孕周/复查", "还不确定", "先放进待确认"),
            )
        )
    if _meaningful(medical_notes):
        topics.append(
            _followup_topic(
                "chronic_medical_condition_coordination",
                "基础疾病或长期用药需要和产科复查、相关专科保持一致。",
                "计划会纳入用药安全确认、专科复查和异常指标联系路径。",
                "目前长期吃药的名称或用途是什么，下一次用药确认或相关专科复查安排在什么时候？还不确定也可以。",
                ("我补充用药/复查", "还不确定", "先放进待确认"),
            )
        )
    if age is not None and age >= 35 and is_multiple:
        topics.append(
            _followup_topic(
                "age_35_plus_multiple_monitoring",
                f"你 {age} 岁且是多胎妊娠，这会同时影响产科管理分层和多胎监测重点。",
                "计划会更早关注血压血糖、胎儿生长差异、宫颈长度、复查频率和早产信号。",
                "目前产检记录里的多胎类型、宫颈长度或胎儿生长差异有没有已经确认的结果？暂无异常或还没确认都可以。",
                ("暂无异常", "还没确认", "我补充一下"),
            )
        )
    elif is_multiple:
        topics.append(
            _followup_topic(
                "multiple_pregnancy_monitoring",
                "多胎妊娠会更关注胎儿生长差异、宫颈情况、复查频率和早产信号。",
                "计划会把多胎类型对应的复查节奏和异常联系路径纳入近期安排。",
                "目前产检记录里的双胎类型是单绒双羊、双绒双羊，还是还没确认？",
                ("单绒双羊", "双绒双羊", "还没确认"),
            )
        )
    if _is_no(plan_context.get("first_birth")) and not _meaningful(prior_history):
        topics.append(
            _followup_topic(
                "prior_birth_history_detail",
                "既往分娩和恢复经历会影响这次分娩沟通、入院准备和产后支持。",
                "计划会保留仍适用的经验，并把上次出现的问题提前纳入准备。",
                "上一胎的分娩方式，以及早产、产后出血或恢复困难等情况有需要纳入这次计划的吗？暂无也可以。",
                ("没有特殊情况", "我补充一下", "先放进待确认"),
            )
        )
    if age is not None and age >= 35 and not is_multiple:
        topics.append(
            _followup_topic(
                "age_35_plus_checkup_detail",
                f"你 {age} 岁，在产科管理上通常会被归入高龄孕产妇范围。",
                "计划会更早关注筛查选择、血压血糖、胎儿生长和复查节奏。",
                "血压/血糖、胎儿生长或甲状腺/免疫或长期用药方面，有没有已经被提醒过或正在复查的项目？暂无异常也可以。",
                ("暂无异常", "正在复查", "还不确定"),
            )
        )
    if _is_yes(plan_context.get("ivf")):
        topics.append(
            _followup_topic(
                "ivf_week_confirmation",
                "IVF/辅助生殖会影响孕周和预产期的确认口径，也可能关联用药复查。",
                "计划会优先对齐医生确认的孕周、移植日期口径、用药与复查节点。",
                "移植日期/孕周口径是否已经由医生确认，目前还有黄体支持或其他需要复核的用药吗？不确定也可以。",
                ("已经确认", "还在用药", "还不确定"),
            )
        )
    if "剖" in str(plan_context.get("birth_path") or "") and not _contains_any(prior_history, ("剖", "c-section", "cesarean")):
        topics.append(
            _followup_topic(
                "planned_c_section_detail",
                "计划剖宫产会影响孕晚期沟通、入院时间和术后支持准备。",
                "计划会提前安排手术评估、术前检查、入院要求和恢复支持。",
                "计划剖宫产主要是因为什么，目前手术评估或大致时间有没有确定？还没确定也可以。",
                ("我补充原因", "时间已确定", "还不确定"),
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
        value = values.get(field_id)
        if isinstance(value, str) and len(value) > 2000:
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
        values.get("delivery_date"),
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
    context = normalize_pregnancy_plan_intake(plan_context)
    for key in (
        "due_date_or_week",
        "delivery_date",
        "birth_setting",
        "feeding_intention",
        "support_person",
        "final_additional_info",
        "scope",
    ):
        value = plan_context.get(key)
        if _has_value(value):
            context[key] = value.strip() if isinstance(value, str) else value
    analysis = analyze_pregnancy_plan_intake(context)
    stage = str(analysis["stage"]["id"])
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
            context.get("delivery_date"),
        )
        or "待确认"
    )
    owner = {
        "due_date_or_week": due,
        "birth_path": _first_text(context.get("birth_path")),
        "birth_setting": _first_text(context.get("birth_setting"), context.get("birth_hospital")),
        "support_person": _first_text(context.get("support_person")),
        "feeding_intention": _first_text(context.get("feeding_intention")),
    }
    return {
        "card_type": "birth_journey_plan_card",
        "schema_version": "1.0",
        "todo_engine_version": "pregnancy-plan-flow-v2",
        "title": "孕期计划",
        "subtitle": "从你现在的孕周开始，把检查、沟通和准备事项按阶段排清楚",
        "owner": {key: value for key, value in owner.items() if _has_value(value)},
        "plan_basis": {
            "stage_summary": analysis["stage"]["summary"],
            "focus_count": len(focus_ids),
            "personalized": len(focus_ids) > 1,
            "additional_information_included": _meaningful(context.get("final_additional_info")),
        },
        "todo_plan": {
            "periods": [
                {
                    "id": "current_stage",
                    "title": _current_period_title(stage=stage, week=analysis["stage"].get("current_week")),
                    "subtitle": "先完成会影响近期检查、沟通和安心感的事项",
                    "display_mode": "expanded",
                    "status": "current",
                    "items": current_items,
                },
                {
                    "id": "labor_and_hospital",
                    "title": "临产与住院",
                    "subtitle": "把去医院、分娩沟通和支持安排提前准备好",
                    "display_mode": "collapsed",
                    "status": "upcoming",
                    "items": _labor_and_hospital_items(),
                },
            ]
        },
        "generation_context": {
            "source": "verified_pregnancy_plan_intake",
            "additional_information_provided": _meaningful(context.get("final_additional_info")),
        },
        "next_action": {"label": "继续整理待产包", "send_text": "帮我整理一份个性化待产包清单"},
        "disclaimer": (
            "这份计划用于准备和沟通，不能替代医生、助产士或医院的具体建议；有破水、出血、胎动明显减少、"
            "规律宫缩加密或明显不适时，请按医院或医生指导处理。"
        ),
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
        "tool_name": "pregnancy.plan.propose",
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
