from __future__ import annotations

from typing import Any

from .assessment import evaluate_milk_status
from .growth import evaluate_infant_growth
from .schemas import PLAN_TYPE_DECREASE, PLAN_TYPE_INCREASE, PLAN_TYPE_MAINTAIN, ServiceResult, norm_text, ok_result, to_bool, to_int

RISK_LOW = "low"
RISK_WATCH = "watch"
RISK_IBCLC_RECOMMENDED = "ibclc_recommended"
RISK_MEDICAL_RECOMMENDED = "medical_recommended"
RISK_URGENT = "urgent"

ALL_PLAN_TYPES = [PLAN_TYPE_INCREASE, PLAN_TYPE_MAINTAIN, PLAN_TYPE_DECREASE]

EVIDENCE_SOURCES: dict[str, dict[str, str]] = {
    "CDC_BREASTFEEDING_FREQUENCY": {
        "title": "CDC breastfeeding frequency and intake signals",
        "source": "CDC",
    },
    "CDC_PUMPING_BREAST_MILK": {
        "title": "CDC pumping breast milk guidance",
        "source": "CDC",
    },
    "ABM_MASTITIS_PROTOCOL": {
        "title": "ABM mastitis spectrum protocol",
        "source": "Academy of Breastfeeding Medicine",
    },
    "ABM_HYPERLACTATION_PROTOCOL": {
        "title": "ABM hyperlactation protocol",
        "source": "Academy of Breastfeeding Medicine",
    },
    "WHO_GROWTH_STANDARDS": {
        "title": "WHO child growth standards",
        "source": "WHO",
    },
}


def evaluate_lactation_clinical_status(
    *,
    user_id: str,
    as_of_time: Any = None,
    window_days: int = 7,
    include_today: bool = False,
    milk_assessment: dict[str, Any] | None = None,
    growth_assessment: dict[str, Any] | None = None,
    maternal_symptoms: dict[str, Any] | None = None,
    infant_signals: dict[str, Any] | None = None,
    requested_plan_type: str | None = None,
) -> ServiceResult:
    milk_result = _ensure_milk_assessment(
        user_id=user_id,
        as_of_time=as_of_time,
        window_days=window_days,
        include_today=include_today,
        milk_assessment=milk_assessment,
    )
    milk_data = milk_result.get("data") if isinstance(milk_result.get("data"), dict) else {}

    growth_data = _ensure_growth_assessment(
        user_id=user_id,
        as_of_time=as_of_time,
        growth_assessment=growth_assessment,
        infant_signals=infant_signals or {},
    )

    domains = {
        "record_completeness": _record_completeness_domain(milk_data, infant_signals or {}, maternal_symptoms or {}),
        "milk_volume": _milk_volume_domain(milk_data),
        "infant_intake": _infant_intake_domain(infant_signals or {}),
        "infant_growth": _infant_growth_domain(growth_data),
        "maternal_breast_symptoms": _maternal_symptoms_domain(maternal_symptoms or {}),
    }
    risk_level, risk_reasons = _resolve_risk(domains)
    plan_gate = _plan_gate(
        risk_level=risk_level,
        domains=domains,
        requested_plan_type=requested_plan_type,
    )
    evidence_ids = _evidence_ids(domains, risk_level)
    next_actions = _next_actions(risk_level, domains, plan_gate)

    data = {
        "risk_level": risk_level,
        "data_confidence": domains["record_completeness"]["data_confidence"],
        "domains": domains,
        "risk_reasons": risk_reasons,
        "plan_gate": plan_gate,
        "next_actions": next_actions,
        "evidence": [_evidence_item(evidence_id) for evidence_id in evidence_ids],
        "milk_assessment": milk_data,
        "growth_assessment": growth_data,
    }
    return ok_result(
        "lactation_clinical_assessment_ready",
        _summary_for_risk(risk_level, plan_gate),
        data,
    )


def _ensure_milk_assessment(
    *,
    user_id: str,
    as_of_time: Any,
    window_days: int,
    include_today: bool,
    milk_assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(milk_assessment, dict) and milk_assessment:
        if "data" in milk_assessment and isinstance(milk_assessment.get("data"), dict):
            return dict(milk_assessment)
        return {"ok": True, "status": "prepared_milk_assessment", "data": dict(milk_assessment)}
    return dict(
        evaluate_milk_status(
            user_id=user_id,
            as_of_time=as_of_time,
            window_days=max(to_int(window_days, 7), 1),
            include_today=include_today,
        )
    )


def _ensure_growth_assessment(
    *,
    user_id: str,
    as_of_time: Any,
    growth_assessment: dict[str, Any] | None,
    infant_signals: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(growth_assessment, dict) and growth_assessment:
        return dict(growth_assessment.get("data") if isinstance(growth_assessment.get("data"), dict) else growth_assessment)
    if _has_growth_related_signal(infant_signals):
        result = evaluate_infant_growth(user_id=user_id, infant_id=None, as_of_time=as_of_time)
        return dict(result.get("data") if isinstance(result.get("data"), dict) else {})
    return {}


def _record_completeness_domain(
    milk_data: dict[str, Any],
    infant_signals: dict[str, Any],
    maternal_symptoms: dict[str, Any],
) -> dict[str, Any]:
    missing = milk_data.get("missing_data") if isinstance(milk_data.get("missing_data"), list) else []
    normality = milk_data.get("milk_normality") if isinstance(milk_data.get("milk_normality"), dict) else {}
    stats = normality.get("stats") if isinstance(normality.get("stats"), dict) else {}
    valid_days = to_int(stats.get("valid_days"), 0)
    window = milk_data.get("window") if isinstance(milk_data.get("window"), dict) else {}
    window_days = max(to_int(window.get("window_days"), 1), 1)

    signal_count = sum(
        1
        for value in (
            infant_signals.get("wet_diapers_24h"),
            infant_signals.get("stool_24h"),
            infant_signals.get("baby_state"),
            infant_signals.get("recent_weight"),
            maternal_symptoms.get("pain_level"),
        )
        if norm_text(value)
    )
    if valid_days <= 0:
        confidence = "low"
    elif valid_days < min(window_days, 3) or missing:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "status": "complete" if confidence == "high" else "partial",
        "data_confidence": confidence,
        "valid_days": valid_days,
        "window_days": window_days,
        "missing_data": missing,
        "extra_signal_count": signal_count,
    }


def _milk_volume_domain(milk_data: dict[str, Any]) -> dict[str, Any]:
    normality = milk_data.get("milk_normality") if isinstance(milk_data.get("milk_normality"), dict) else {}
    status = norm_text(normality.get("overall_status")) or "unknown"
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    low_days = [item for item in valid if norm_text(item.get("status")) == "low"]
    high_days = [item for item in valid if norm_text(item.get("status")) == "high"]
    latest = sorted(valid, key=lambda item: norm_text(item.get("date")))[-1] if valid else {}
    return {
        "status": status,
        "latest_status": norm_text(latest.get("status")) or "unknown",
        "valid_days": len(valid),
        "low_days": len(low_days),
        "high_days": len(high_days),
        "estimated_daily_milk_ml": latest.get("estimated_daily_milk_ml"),
        "estimated_breastfeeding_ml": latest.get("estimated_breastfeeding_ml"),
        "rule_hit": latest.get("rule_hit"),
    }


def _infant_intake_domain(infant_signals: dict[str, Any]) -> dict[str, Any]:
    wet_diapers = _optional_int(infant_signals.get("wet_diapers_24h"))
    baby_state = norm_text(infant_signals.get("baby_state")).lower()
    poor_intake = to_bool(infant_signals.get("poor_feeding")) or to_bool(infant_signals.get("poor_latch"))
    lethargic = to_bool(infant_signals.get("lethargy")) or any(token in baby_state for token in ("嗜睡", "精神差", "无力", "letharg"))
    fewer_wet_diapers = wet_diapers is not None and wet_diapers < 4
    if lethargic or fewer_wet_diapers or poor_intake:
        status = "concern"
    elif wet_diapers is None and not baby_state:
        status = "unknown"
    else:
        status = "reassuring"
    return {
        "status": status,
        "wet_diapers_24h": wet_diapers,
        "baby_state": infant_signals.get("baby_state"),
        "poor_feeding": poor_intake,
        "lethargy": lethargic,
    }


def _infant_growth_domain(growth_data: dict[str, Any]) -> dict[str, Any]:
    status = norm_text(growth_data.get("status") or growth_data.get("overall_status"))
    if not status:
        status = "unknown"
    normalized = "concern" if status in {"abnormal", "drop", "significant_drop", "low", "high"} else status
    return {
        "status": normalized,
        "raw_status": status,
        "summary": growth_data.get("summary"),
    }


def _maternal_symptoms_domain(maternal_symptoms: dict[str, Any]) -> dict[str, Any]:
    fever = to_bool(maternal_symptoms.get("fever"))
    chills = to_bool(maternal_symptoms.get("chills"))
    redness = to_bool(maternal_symptoms.get("breast_redness"))
    lump = to_bool(maternal_symptoms.get("lump_or_hard_area"))
    worsening_pain = to_bool(maternal_symptoms.get("worsening_pain"))
    nipple_damage = to_bool(maternal_symptoms.get("nipple_damage"))
    recurrent_plug = to_bool(maternal_symptoms.get("recurrent_plug"))
    pain_level = _optional_int(maternal_symptoms.get("pain_level"))

    if fever and (chills or redness or lump or worsening_pain):
        status = "medical_concern"
    elif redness or lump or recurrent_plug or nipple_damage or (pain_level is not None and pain_level >= 6):
        status = "ibclc_concern"
    elif maternal_symptoms:
        status = "reassuring"
    else:
        status = "unknown"
    return {
        "status": status,
        "fever": fever,
        "chills": chills,
        "breast_redness": redness,
        "lump_or_hard_area": lump,
        "worsening_pain": worsening_pain,
        "nipple_damage": nipple_damage,
        "recurrent_plug": recurrent_plug,
        "pain_level": pain_level,
    }


def _resolve_risk(domains: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    maternal = domains["maternal_breast_symptoms"]
    infant_intake = domains["infant_intake"]
    growth = domains["infant_growth"]
    milk = domains["milk_volume"]
    completeness = domains["record_completeness"]

    if maternal.get("status") == "medical_concern":
        reasons.append("妈妈有发热/寒战合并乳房红肿、硬块或疼痛加重信号。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if infant_intake.get("lethargy") is True:
        reasons.append("宝宝精神状态提示需要优先专业评估。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if infant_intake.get("status") == "concern" and growth.get("status") == "concern":
        reasons.append("宝宝摄入和生长信号都需要关注。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if maternal.get("status") == "ibclc_concern":
        reasons.append("妈妈有乳房不适、乳头损伤或反复堵奶信号。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if infant_intake.get("status") == "concern":
        reasons.append("宝宝摄入信号不够安心。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if growth.get("status") == "concern":
        reasons.append("宝宝生长信号需要结合专业评估。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if completeness.get("data_confidence") == "low":
        reasons.append("关键记录不足，暂时不适合下确定判断。")
        return RISK_WATCH, reasons
    if milk.get("status") in {"under_supply_alert", "over_supply_alert"} or milk.get("latest_status") in {"low", "high"}:
        reasons.append("奶量数据出现连续或关键阶段偏离参考的信号。")
        return RISK_WATCH, reasons
    return RISK_LOW, reasons


def _plan_gate(*, risk_level: str, domains: dict[str, dict[str, Any]], requested_plan_type: str | None) -> dict[str, Any]:
    requested = norm_text(requested_plan_type)
    milk_status = norm_text(domains["milk_volume"].get("status"))
    maternal_status = norm_text(domains["maternal_breast_symptoms"].get("status"))
    infant_status = norm_text(domains["infant_intake"].get("status"))
    growth_status = norm_text(domains["infant_growth"].get("status"))
    confidence = norm_text(domains["record_completeness"].get("data_confidence"))

    if risk_level in {RISK_MEDICAL_RECOMMENDED, RISK_URGENT}:
        return {
            "allowed": False,
            "allowed_plan_types": [],
            "blocked_plan_types": ALL_PLAN_TYPES,
            "reason": "当前有需要优先医学评估的信号，先不要进入普通奶量计划。",
        }
    if risk_level == RISK_IBCLC_RECOMMENDED:
        allowed = [PLAN_TYPE_MAINTAIN]
        return {
            "allowed": requested in {"", PLAN_TYPE_MAINTAIN},
            "allowed_plan_types": allowed,
            "blocked_plan_types": [item for item in ALL_PLAN_TYPES if item not in allowed],
            "reason": "当前更适合先看含乳、移乳效率或乳房不适，再决定是否追奶/减奶。",
        }
    if confidence == "low":
        return {
            "allowed": False,
            "allowed_plan_types": [],
            "blocked_plan_types": ALL_PLAN_TYPES,
            "reason": "关键记录不足，先补充记录后再生成计划。",
        }
    if requested == PLAN_TYPE_DECREASE and (maternal_status in {"ibclc_concern", "medical_concern"} or infant_status == "concern"):
        return {
            "allowed": False,
            "allowed_plan_types": [PLAN_TYPE_MAINTAIN],
            "blocked_plan_types": [PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE],
            "reason": "有乳房不适或宝宝摄入信号时，不适合直接减奶。",
        }
    if requested == PLAN_TYPE_DECREASE and milk_status != "over_supply_alert" and growth_status == "concern":
        return {
            "allowed": False,
            "allowed_plan_types": [PLAN_TYPE_MAINTAIN],
            "blocked_plan_types": [PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE],
            "reason": "宝宝生长信号不稳时，不适合直接减奶。",
        }
    return {
        "allowed": True,
        "allowed_plan_types": ALL_PLAN_TYPES,
        "blocked_plan_types": [],
        "reason": "当前没有阻断普通奶量计划的高风险信号。",
    }


def _next_actions(risk_level: str, domains: dict[str, dict[str, Any]], plan_gate: dict[str, Any]) -> list[str]:
    if risk_level == RISK_MEDICAL_RECOMMENDED:
        return ["优先联系医生/医院或 IBCLC。", "暂停普通追奶、稳奶或减奶计划判断。"]
    if risk_level == RISK_IBCLC_RECOMMENDED:
        return ["建议让 IBCLC 一起看含乳、移乳效率和乳房不适。", "先记录 24 小时尿布、喂养和乳房舒适度。"]
    if not plan_gate.get("allowed"):
        return ["先补充关键记录。", "记录完整后再判断是否生成奶量计划。"]
    if domains["milk_volume"].get("status") == "under_supply_alert":
        return ["可以生成温和追奶计划。", "连续记录 3 天后复盘宝宝信号和妈妈舒适度。"]
    if domains["milk_volume"].get("status") == "over_supply_alert":
        return ["先观察 3-5 天。", "如果持续胀痛、堵奶或喷乳明显，再考虑温和减奶。"]
    return ["可以继续按当前节奏观察。", "需要时可生成稳奶计划。"]


def _evidence_ids(domains: dict[str, dict[str, Any]], risk_level: str) -> list[str]:
    ids = ["CDC_BREASTFEEDING_FREQUENCY"]
    if domains["maternal_breast_symptoms"].get("status") in {"medical_concern", "ibclc_concern"}:
        ids.append("ABM_MASTITIS_PROTOCOL")
    if domains["milk_volume"].get("status") == "over_supply_alert":
        ids.append("ABM_HYPERLACTATION_PROTOCOL")
    if domains["infant_growth"].get("status") == "concern":
        ids.append("WHO_GROWTH_STANDARDS")
    if risk_level in {RISK_LOW, RISK_WATCH}:
        ids.append("CDC_PUMPING_BREAST_MILK")
    return list(dict.fromkeys(ids))


def _evidence_item(evidence_id: str) -> dict[str, str]:
    source = EVIDENCE_SOURCES.get(evidence_id, {})
    return {
        "id": evidence_id,
        "title": source.get("title", evidence_id),
        "source": source.get("source", ""),
    }


def _summary_for_risk(risk_level: str, plan_gate: dict[str, Any]) -> str:
    if risk_level == RISK_MEDICAL_RECOMMENDED:
        return "当前有需要优先医学评估的信号。"
    if risk_level == RISK_IBCLC_RECOMMENDED:
        return "当前建议优先结合 IBCLC 评估。"
    if not plan_gate.get("allowed"):
        return "当前数据还不足以生成奶量计划。"
    if risk_level == RISK_WATCH:
        return "当前适合谨慎观察，并可按规则生成温和计划。"
    return "当前未发现阻断普通奶量计划的高风险信号。"


def _has_growth_related_signal(infant_signals: dict[str, Any]) -> bool:
    return any(
        norm_text(infant_signals.get(key))
        for key in ("recent_weight", "weight_trend", "growth_concern", "baby_state", "wet_diapers_24h")
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
