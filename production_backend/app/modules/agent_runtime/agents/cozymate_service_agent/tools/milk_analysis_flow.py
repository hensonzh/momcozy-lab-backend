from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


MILK_ANALYSIS_FIELDS = (
    "records_7d",
    "infant_wet_diapers",
    "infant_state_or_satisfaction",
    "infant_growth_signal",
    "maternal_red_flags",
    "maternal_breast_comfort",
)

MILK_ANALYSIS_QUESTIONS = {
    "infant_wet_diapers": "宝宝近 24 小时大约有几片明显湿尿布？",
    "infant_state_or_satisfaction": "宝宝精神状态怎么样，吃奶后通常能安稳下来吗？",
    "infant_growth_signal": "宝宝近期体重增长是正常、偏慢，还是还没有称重？",
    "maternal_red_flags": "你现在有没有发热、寒战、乳房明显红肿、硬块或疼痛加重？",
    "maternal_breast_comfort": "吸奶或亲喂后，乳房是舒服些，还是仍会胀、排不空或疼？",
}

MILK_ANALYSIS_WORKFLOW_TYPE = "milk_analysis"
MILK_ANALYSIS_SCHEMA_VERSION = "milk_analysis.v1"


class MilkAnalysisFlowError(ValueError):
    pass


def initialize_milk_analysis_intake(*, records_snapshot: dict[str, Any]) -> dict[str, Any]:
    workflow = {
        "phase": "collecting_intake",
        "records_snapshot": deepcopy(records_snapshot),
        "answers": {},
    }
    return _project_intake(workflow)


def advance_milk_analysis_intake(workflow: dict[str, Any], *, answer: str) -> dict[str, Any]:
    projected = _project_intake(workflow)
    current_field = _text(projected.get("current_field"))
    if not current_field:
        raise MilkAnalysisFlowError("milk_analysis_intake_already_complete")
    normalized_answer = _text(answer)
    if not normalized_answer:
        raise MilkAnalysisFlowError("milk_analysis_answer_required")
    raw_answers = projected.get("answers")
    answers: dict[str, Any] = dict(raw_answers) if isinstance(raw_answers, dict) else {}
    answers[current_field] = normalized_answer
    projected["answers"] = answers
    return _project_intake(projected)


def build_milk_analysis_assessment(workflow: dict[str, Any]) -> dict[str, Any]:
    projected = _project_intake(workflow)
    if projected.get("phase") != "ready_to_evaluate":
        raise MilkAnalysisFlowError("milk_analysis_intake_incomplete")

    raw_snapshot = projected.get("records_snapshot")
    snapshot: dict[str, Any] = dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
    raw_answers = projected.get("answers")
    answers: dict[str, Any] = dict(raw_answers) if isinstance(raw_answers, dict) else {}
    maternal_red_flags = _has_maternal_red_flags(_text(answers.get("maternal_red_flags")))
    infant_intake_risk = _has_infant_intake_risk(answers)
    data_coverage = _data_coverage(snapshot)
    trend = _trend(snapshot)
    direction = _recommended_direction(snapshot=snapshot, trend=trend)

    plan_decision: dict[str, Any]
    if maternal_red_flags:
        plan_decision = {
            "can_start_plan": False,
            "recommended_direction": None,
            "reason": "maternal_red_flags_require_professional_support",
        }
    elif infant_intake_risk:
        plan_decision = {
            "can_start_plan": False,
            "recommended_direction": None,
            "reason": "infant_intake_signals_require_professional_support",
        }
    elif data_coverage == "no_recent_data":
        plan_decision = {
            "can_start_plan": False,
            "recommended_direction": None,
            "reason": "recent_records_required_before_plan",
        }
    elif direction is None:
        plan_decision = {
            "can_start_plan": False,
            "recommended_direction": None,
            "reason": "no_supported_plan_direction",
        }
    else:
        reason = {
            "increase": "recent_milk_below_expected_without_safety_block",
            "maintain": "recent_milk_stable_without_safety_block",
            "decrease": "recent_milk_above_expected_without_safety_block",
        }[direction]
        plan_decision = {"can_start_plan": True, "recommended_direction": direction, "reason": reason}

    context: dict[str, Any] = {
        "schema_version": MILK_ANALYSIS_SCHEMA_VERSION,
        "records_snapshot": snapshot,
        "answers": answers,
    }
    fingerprint = milk_analysis_context_fingerprint(context)
    assessment: dict[str, Any] = {
        "phase": "assessment_complete",
        "analysis_context": context,
        "analysis_context_fingerprint": fingerprint,
        "risk": {
            "maternal_red_flags": maternal_red_flags,
            "infant_intake_risk": infant_intake_risk,
        },
        "eligibility": {
            "data_coverage": data_coverage,
            "trend": trend,
            "direction": direction,
        },
        "plan_decision": plan_decision,
    }
    assessment["card"] = _milk_analysis_card(assessment)
    return assessment


def milk_analysis_context_fingerprint(context: dict[str, Any]) -> str:
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _project_intake(workflow: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(workflow)
    raw_answers = projected.get("answers")
    answers: dict[str, Any] = dict(raw_answers) if isinstance(raw_answers, dict) else {}
    projected["answers"] = answers
    checklist: list[dict[str, str]] = [{"id": "records_7d", "status": "collected"}]
    current_field = ""
    for field in MILK_ANALYSIS_FIELDS[1:]:
        collected = bool(_text(answers.get(field)))
        checklist.append({"id": field, "status": "collected" if collected else "missing"})
        if not collected and not current_field:
            current_field = field
    completed_count = len([item for item in checklist if item["status"] == "collected"])
    projected.update(
        {
            "phase": "collecting_intake" if current_field else "ready_to_evaluate",
            "checklist": checklist,
            "current_field": current_field or None,
            "next_question": MILK_ANALYSIS_QUESTIONS.get(current_field, ""),
            "progress": {
                "index": min(completed_count + 1, len(MILK_ANALYSIS_FIELDS)),
                "total": len(MILK_ANALYSIS_FIELDS),
                "completed_count": completed_count,
                "remaining_count": len(MILK_ANALYSIS_FIELDS) - completed_count,
            },
        }
    )
    return projected


def _data_coverage(snapshot: dict[str, Any]) -> str:
    raw_analysis = snapshot.get("analysis")
    analysis: dict[str, Any] = dict(raw_analysis) if isinstance(raw_analysis, dict) else {}
    raw_status = snapshot.get("status")
    status: dict[str, Any] = dict(raw_status) if isinstance(raw_status, dict) else {}
    explicit = _text(analysis.get("data_coverage")) or _text(status.get("data_coverage"))
    if explicit:
        return explicit
    raw_counts = snapshot.get("counts")
    counts: dict[str, Any] = dict(raw_counts) if isinstance(raw_counts, dict) else {}
    has_records = any(int(counts.get(key) or 0) > 0 for key in ("recent_feedings", "recent_pumpings"))
    return "ready" if has_records else "no_recent_data"


def _trend(snapshot: dict[str, Any]) -> str:
    raw_analysis = snapshot.get("analysis")
    analysis: dict[str, Any] = dict(raw_analysis) if isinstance(raw_analysis, dict) else {}
    raw_status = snapshot.get("status")
    status: dict[str, Any] = dict(raw_status) if isinstance(raw_status, dict) else {}
    return _text(analysis.get("pumping_trend")) or _text(status.get("pumping_trend")) or "insufficient_data"


def _recommended_direction(*, snapshot: dict[str, Any], trend: str) -> str | None:
    raw_analysis = snapshot.get("analysis")
    analysis: dict[str, Any] = dict(raw_analysis) if isinstance(raw_analysis, dict) else {}
    explicit = _text(analysis.get("status")) or (_text(snapshot.get("status")) if not isinstance(snapshot.get("status"), dict) else "")
    if explicit in {"under_supply_alert", "low", "below_expected"} or trend == "decreasing":
        return "increase"
    if explicit in {"over_supply_alert", "high", "above_expected"} or trend == "increasing":
        return "decrease"
    if explicit in {"normal", "stable"} or trend == "stable":
        return "maintain"
    if _data_coverage(snapshot) == "ready":
        return "maintain"
    return None


def _has_maternal_red_flags(answer: str) -> bool:
    text = answer.replace(" ", "")
    if not text:
        return True
    contrast_parts = [part for token in ("但是", "但", "不过", "可是") for part in text.split(token)[1:] if part]
    if contrast_parts:
        return any(_positive_red_flag_text(part) for part in contrast_parts)
    if text.startswith(("没有", "无", "未出现", "都没有", "并没有", "不")):
        return False
    return _positive_red_flag_text(text)


def _positive_red_flag_text(text: str) -> bool:
    positive_phrases = ("发热", "发烧", "寒战", "红肿", "硬块", "疼痛加重", "越来越痛")
    return any(phrase in text for phrase in positive_phrases)


def _has_infant_intake_risk(answers: dict[str, Any]) -> bool:
    wet = _text(answers.get("infant_wet_diapers")).replace(" ", "")
    state = _text(answers.get("infant_state_or_satisfaction")).replace(" ", "")
    growth = _text(answers.get("infant_growth_signal")).replace(" ", "")
    for phrase in ("没有明显变少", "尿布不少", "没有变少"):
        wet = wet.replace(phrase, "")
    for phrase in ("精神不差", "没有嗜睡", "不会叫不醒"):
        state = state.replace(phrase, "")
    for phrase in ("体重没有下降", "没有下降", "不是没长"):
        growth = growth.replace(phrase, "")
    return (
        any(token in wet for token in ("尿布很少", "不到4", "不到四", "明显变少"))
        or any(token in state for token in ("精神差", "嗜睡", "叫不醒", "一直不满足"))
        or any(token in growth for token in ("增长很慢", "体重下降", "没长"))
    )


def _milk_analysis_card(assessment: dict[str, Any]) -> dict[str, Any]:
    context = assessment["analysis_context"]
    snapshot = context["records_snapshot"]
    answers = context["answers"]
    decision = assessment["plan_decision"]
    direction_labels = {
        "increase": "更适合先讨论追奶计划",
        "maintain": "可继续当前节奏或制定稳奶计划",
        "decrease": "更适合先讨论温和减奶计划",
    }
    if decision["can_start_plan"]:
        headline = direction_labels.get(decision["recommended_direction"], "已完成奶量分析")
    elif assessment["risk"]["maternal_red_flags"]:
        headline = "当前先处理乳房或全身不适，再考虑奶量计划"
    elif assessment["risk"]["infant_intake_risk"]:
        headline = "当前先确认宝宝摄入和生长信号，再考虑奶量计划"
    else:
        headline = "近期记录还不足，先补记录再判断"
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    volumes = snapshot.get("volumes") if isinstance(snapshot.get("volumes"), dict) else {}
    return {
        "card_type": "milk_analysis_card",
        "title": "奶量分析",
        "status": "completed",
        "headline": headline,
        "sections": [
            {
                "id": "milk",
                "title": "近 7 天记录",
                "metrics": [
                    {"label": "吸奶记录", "value": str(int(counts.get("recent_pumpings") or 0))},
                    {"label": "近期吸出", "value": f"{float(volumes.get('recent_pumped_volume_ml') or 0):g} ml"},
                ],
            },
            {
                "id": "signals",
                "title": "宝宝和妈妈状态",
                "items": [
                    _text(answers.get("infant_wet_diapers")),
                    _text(answers.get("infant_state_or_satisfaction")),
                    _text(answers.get("infant_growth_signal")),
                    _text(answers.get("maternal_red_flags")),
                    _text(answers.get("maternal_breast_comfort")),
                ],
            },
            {"id": "next", "title": "下一步", "items": [headline]},
        ],
        "can_start_plan": decision["can_start_plan"],
        "recommended_direction": decision["recommended_direction"],
    }


def _text(value: Any) -> str:
    return str(value or "").strip()
