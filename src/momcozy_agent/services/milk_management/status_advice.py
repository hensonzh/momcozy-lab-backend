from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from ...config import load_project_env
from .. import data_store
from .assessment import evaluate_milk_status
from .feeding import estimate_breastfeeding_milk


SIMPLE_STATUS_ADVICE_PROMPT = """
你是 Momcozy 的泌乳和喂养状态建议助手。根据输入的生产日期、宝宝信息，以及近7天吸奶/亲喂/喂奶记录，生成简短自然语言建议。

要求：
- 只返回 JSON：{"lactation_advice":"...","feeding_advice":"..."}
- 每条 advice 必须是中文，50字以内。
- 语气亲和、鼓励，温柔，有朋友的感觉，千万不制造焦虑。
- 不诊断、不承诺奶量一定够或不够，不替代医生、儿科医生或 IBCLC。
- 有记录时，只基于记录做轻量总结和下一步建议。
- 记录少时，可以提醒建议仅供参考，并鼓励继续记录。
- 如果近7天没有任何吸奶、亲喂或喂奶记录：根据 delivery_date/postpartum_days 给通用建议，并温和提醒养成记录习惯。
- 如果输入包含 analysis_normality：advice 必须和 result 方向一致。
- 当 result=true：以保持节奏和继续观察为主，不要求增加、减少或明显改进。
- 当 result=false：必须根据 failed_metrics/reason 指出具体可调整方向，不能两条都只夸奖。
- lactation_advice 只关注泌乳/吸奶/亲喂排乳方向；feeding_advice 只关注宝宝喂养频次/摄入记录方向。
""".strip()


def generate_status_advice(*, user_id: str, days: int = 7, normality: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Generate both lactation and feeding advice for status creation."""

    uid = str(user_id or "").strip()
    if not uid:
        return None

    context = data_store.get_status_advice_context(user_id=uid, days=days)
    if not context:
        return None

    payload = _build_llm_payload(context, normality=normality)
    generated = _request_llm_status_advice(payload)
    if not generated:
        generated = _fallback_status_advice(payload, normality=normality)
    if not generated.get("lactation_advice", "").strip() or not generated.get("feeding_advice", "").strip():
        return None
    return generated


def evaluate_status_advice_normality(*, user_id: str, days: int = 7, min_valid_days: int = 3) -> dict[str, Any]:
    uid = str(user_id or "").strip()
    if not uid:
        return _normality_result(False, False, "missing_user_id", days=[], window_days=days, min_valid_days=min_valid_days)

    assessment = evaluate_milk_status(user_id=uid, window_days=days, include_today=False)
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    milk_normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    day_items = milk_normality.get("days") if isinstance(milk_normality.get("days"), list) else []
    if not assessment.get("ok"):
        return _normality_result(False, False, "assessment_failed", days=day_items, window_days=days, min_valid_days=min_valid_days)

    valid_days = [item for item in day_items if isinstance(item, dict) and item.get("ok") is True]
    if len(valid_days) < min_valid_days:
        return _normality_result(False, False, "insufficient_minimum_valid_days", days=day_items, window_days=days, min_valid_days=min_valid_days)

    lactation_normal = _lactation_normal_for_days(valid_days)
    feeding_normal = _feeding_normal_for_days(valid_days)
    if not lactation_normal:
        reason = "lactation_out_of_range"
    elif not feeding_normal:
        reason = "feeding_out_of_range"
    else:
        reason = "normal"
    return _normality_result(lactation_normal, feeding_normal, reason, days=valid_days, window_days=days, min_valid_days=min_valid_days)


def _build_llm_payload(context: dict[str, Any], *, normality: dict[str, Any] | None = None) -> dict[str, Any]:
    user_profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
    infant_profile = context.get("infant_profile") if isinstance(context.get("infant_profile"), dict) else {}
    pumping_records = context.get("pumping_records") if isinstance(context.get("pumping_records"), list) else []
    feeding_records = context.get("feeding_records") if isinstance(context.get("feeding_records"), list) else []

    payload = {
        "user_id": str(user_profile.get("user_id") or infant_profile.get("user_id") or ""),
        "delivery_date": str(user_profile.get("delivery_date") or infant_profile.get("birth_date") or ""),
        "postpartum_days": _days_since(user_profile.get("delivery_date") or infant_profile.get("birth_date")),
        "infant": {
            "infant_id": infant_profile.get("infant_id"),
            "birth_date": str(infant_profile.get("birth_date") or ""),
            "age_days": _days_since(infant_profile.get("birth_date")),
            "sex": str(infant_profile.get("sex") or ""),
        },
        "window": context.get("window") if isinstance(context.get("window"), dict) else {},
        "has_any_recent_record": bool(pumping_records or feeding_records),
        "pumping_summary": _summarize_pumping(
            pumping_records,
            user_id=str(user_profile.get("user_id") or infant_profile.get("user_id") or ""),
            as_of_time=(context.get("window") if isinstance(context.get("window"), dict) else {}).get("end_at"),
        ),
        "feeding_summary": _summarize_feeding(feeding_records),
    }
    if isinstance(normality, dict):
        payload["analysis_normality"] = normality
    return payload


def _lactation_normal_for_days(day_items: list[dict[str, Any]]) -> bool:
    return all(item.get("normal") is True for item in day_items)


def _feeding_normal_for_days(day_items: list[dict[str, Any]]) -> bool:
    for item in day_items:
        reference = item.get("frequency_reference") if isinstance(item.get("frequency_reference"), dict) else {}
        p25 = _int(reference.get("p25"), 0)
        p75 = _int(reference.get("p75"), 0)
        feeding_count = _int(item.get("feeding_count_total"), 0)
        if p25 <= 0 or p75 <= 0 or feeding_count < p25 or feeding_count > p75:
            return False
    return True


def _normality_result(lactation_normal: bool, feeding_normal: bool, reason: str, *, days: list[Any], window_days: int = 7, min_valid_days: int = 3) -> dict[str, Any]:
    metrics = [_normality_day_metrics(item) for item in days if isinstance(item, dict)]
    failed_metrics = _failed_metrics(lactation_normal, feeding_normal, reason, metrics)
    return {
        "result": bool(lactation_normal and feeding_normal),
        "lactation_normal": bool(lactation_normal),
        "feeding_normal": bool(feeding_normal),
        "reason": reason,
        "failed_metrics": failed_metrics,
        "window_days": int(window_days),
        "min_valid_days": int(min_valid_days),
        "include_today": False,
        "evaluated_days": len(days),
        "metrics": metrics,
    }


def _normality_day_metrics(item: dict[str, Any]) -> dict[str, Any]:
    reference = item.get("frequency_reference") if isinstance(item.get("frequency_reference"), dict) else {}
    return {
        "date": str(item.get("date") or ""),
        "normal": item.get("normal"),
        "status": str(item.get("status") or ""),
        "rule_hit": str(item.get("rule_hit") or ""),
        "pumping_count": _int(item.get("pumping_count"), 0),
        "breastfeeding_count": _int(item.get("breastfeeding_count"), 0),
        "feeding_count_total": _int(item.get("feeding_count_total"), 0),
        "estimated_daily_milk_ml": _float(item.get("estimated_daily_milk_ml")),
        "feeding_frequency_p25": _int(reference.get("p25"), 0),
        "feeding_frequency_p75": _int(reference.get("p75"), 0),
    }


def _failed_metrics(lactation_normal: bool, feeding_normal: bool, reason: str, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    if not lactation_normal:
        failed.append({"type": "lactation", "reason": reason, "days": [item for item in metrics if item.get("normal") is not True]})
    if not feeding_normal:
        failed.append(
            {
                "type": "feeding",
                "reason": reason,
                "days": [
                    item
                    for item in metrics
                    if item.get("feeding_frequency_p25") and item.get("feeding_frequency_p75")
                    and not (item["feeding_frequency_p25"] <= item.get("feeding_count_total", 0) <= item["feeding_frequency_p75"])
                ],
            }
        )
    return failed


def _fallback_status_advice(payload: dict[str, Any], *, normality: dict[str, Any] | None = None) -> dict[str, str]:
    if not payload.get("has_any_recent_record"):
        return {
            "lactation_advice": "最近记录还不多，先连续记录吸奶和亲喂几天。",
            "feeding_advice": "先把喂养次数和奶量记完整，后面更好判断。",
        }

    norm = normality if isinstance(normality, dict) else {}
    if norm.get("result") is True:
        return {
            "lactation_advice": "近几天奶量暂无明显异常，继续保持当前节奏。",
            "feeding_advice": "喂养次数整体在参考范围内，继续观察尿布和精神。",
        }

    reason = str(norm.get("reason") or "").strip()
    if reason == "insufficient_minimum_valid_days":
        return {
            "lactation_advice": "有效记录还不够，先连续记录吸奶和亲喂几天。",
            "feeding_advice": "喂养记录还不够完整，先把次数和奶量补起来。",
        }

    failed_metrics = norm.get("failed_metrics") if isinstance(norm.get("failed_metrics"), list) else []
    failed_types = {
        str(item.get("type") or "").strip()
        for item in failed_metrics
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }
    if len(failed_types) > 1:
        return {
            "lactation_advice": "奶量和喂养都有波动，先把吸奶、亲喂分开记录。",
            "feeding_advice": "这两天先按需喂养，同时观察尿布、精神和体重。",
        }

    if "lactation" in failed_types or reason == "lactation_out_of_range":
        statuses = _failed_day_statuses(failed_metrics, metric_type="lactation")
        if "low" in statuses and "high" not in statuses:
            lactation_text = "近几天奶量低于参考，先稳定排乳间隔和记录。"
        elif "high" in statuses and "low" not in statuses:
            lactation_text = "近几天奶量高于参考，先留意胀痛和排乳舒适度。"
        else:
            lactation_text = "奶量有波动，先固定记录时间观察连续趋势。"
        return {
            "lactation_advice": lactation_text,
            "feeding_advice": "喂养次数先继续观察，重点同步看尿布和精神。",
        }

    if "feeding" in failed_types or reason == "feeding_out_of_range":
        directions = _failed_feeding_directions(failed_metrics)
        if "low" in directions and "high" not in directions:
            feeding_text = "喂养次数偏少，先留意宝宝精神、尿布和进食间隔。"
        elif "high" in directions and "low" not in directions:
            feeding_text = "喂养次数偏多，先观察每次摄入和宝宝满足感。"
        else:
            feeding_text = "喂养频次有波动，先连续记录几天再看趋势。"
        return {
            "lactation_advice": "排乳记录可以继续保持，重点同步观察宝宝需求。",
            "feeding_advice": feeding_text,
        }

    return {
        "lactation_advice": "近几天记录有波动，先稳定排乳节奏并继续观察。",
        "feeding_advice": "喂养记录继续补完整，优先观察尿布、精神和体重。",
    }


def _failed_day_statuses(failed_metrics: list[Any], *, metric_type: str) -> set[str]:
    statuses: set[str] = set()
    for metric in failed_metrics:
        if not isinstance(metric, dict) or str(metric.get("type") or "") != metric_type:
            continue
        days = metric.get("days") if isinstance(metric.get("days"), list) else []
        for day in days:
            if not isinstance(day, dict):
                continue
            status = str(day.get("status") or "").strip()
            if status:
                statuses.add(status)
    return statuses


def _failed_feeding_directions(failed_metrics: list[Any]) -> set[str]:
    directions: set[str] = set()
    for metric in failed_metrics:
        if not isinstance(metric, dict) or str(metric.get("type") or "") != "feeding":
            continue
        days = metric.get("days") if isinstance(metric.get("days"), list) else []
        for day in days:
            if not isinstance(day, dict):
                continue
            feeding_count = _safe_float(day.get("feeding_count_total"))
            p25 = _safe_float(day.get("feeding_frequency_p25"))
            p75 = _safe_float(day.get("feeding_frequency_p75"))
            if p25 > 0 and feeding_count < p25:
                directions.add("low")
            if p75 > 0 and feeding_count > p75:
                directions.add("high")
    return directions


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _summarize_pumping(records: list[Any], *, user_id: str, as_of_time: Any = None) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    daily_totals: dict[str, float] = {}
    total_ml = 0.0
    breastfeeding_count = 0
    breastfeeding_estimate = estimate_breastfeeding_milk(user_id=user_id, as_of_time=as_of_time)

    for raw in records:
        if not isinstance(raw, dict):
            continue
        pump_type = _int(raw.get("pump_type"), 0)
        volume_ml = _float(raw.get("pump_milk_volum"))
        duration_minutes = _int_or_none(raw.get("pump_milk_duration"))
        if pump_type == 2:
            breastfeeding_count += 1
            effective_ml = breastfeeding_estimate or 0.0
        else:
            effective_ml = volume_ml
        if effective_ml <= 0 and (duration_minutes or 0) <= 0:
            continue

        time_text = str(raw.get("pump_start_time") or "")
        date_key = time_text[:10]
        total_ml += effective_ml
        if date_key:
            daily_totals[date_key] = daily_totals.get(date_key, 0.0) + effective_ml
        normalized.append(
            {
                "time": time_text,
                "pump_type": pump_type,
                "pump_source": _int(raw.get("pump_source"), 1),
                "volume_ml": round(volume_ml, 1),
                "duration_minutes": duration_minutes,
                "estimated_breastfeeding_ml": round(breastfeeding_estimate, 1) if breastfeeding_estimate is not None else None,
                "title": str(raw.get("pump_title") or ""),
            }
        )

    return {
        "has_records": bool(normalized),
        "record_count": len(normalized),
        "breastfeeding_count": breastfeeding_count,
        "total_effective_ml": round(total_ml, 1),
        "daily_totals": [{"date": key, "ml": round(value, 1)} for key, value in sorted(daily_totals.items())],
        "records": normalized[-30:],
    }


def _summarize_feeding(records: list[Any]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    daily_totals: dict[str, float] = {}
    bottle_or_formula_total = 0.0
    breastfeeding_count = 0
    formula_count = 0

    for raw in records:
        if not isinstance(raw, dict):
            continue
        feed_type = str(raw.get("feed_type") or "")
        amount = _float(raw.get("feed_milk_volum"))
        if amount <= 0:
            continue
        time_text = str(raw.get("feed_time") or "")
        date_key = time_text[:10]
        if feed_type == data_store.FEED_TYPE_CODE_TO_TEXT[0]:
            breastfeeding_count += 1
            amount_kind = "duration_minutes"
        else:
            amount_kind = "volume_ml"
            bottle_or_formula_total += amount
            if date_key:
                daily_totals[date_key] = daily_totals.get(date_key, 0.0) + amount
        if feed_type == data_store.FEED_TYPE_CODE_TO_TEXT[2]:
            formula_count += 1
        normalized.append(
            {
                "time": time_text,
                "feed_type": feed_type,
                "feed_action": _int(raw.get("feed_action"), 0),
                amount_kind: round(amount, 1),
                "title": str(raw.get("feeding_title") or ""),
            }
        )

    return {
        "has_records": bool(normalized),
        "record_count": len(normalized),
        "breastfeeding_count": breastfeeding_count,
        "formula_count": formula_count,
        "bottle_or_formula_total_ml": round(bottle_or_formula_total, 1),
        "daily_bottle_or_formula_totals": [{"date": key, "ml": round(value, 1)} for key, value in sorted(daily_totals.items())],
        "records": normalized[-30:],
    }


def _request_llm_status_advice(payload: dict[str, Any]) -> dict[str, str] | None:
    load_project_env()
    try:
        from openai import OpenAI
    except ImportError:
        return None

    try:
        client = OpenAI()
        response = client.responses.create(
            model=os.getenv("STATUS_ADVICE_MODEL", "gpt-5.4-mini"),
            instructions=SIMPLE_STATUS_ADVICE_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }
                    ],
                }
            ],
            reasoning={"effort": "low"},
            text={"format": {"type": "text"}, "verbosity": "low"},
            store=False,
            prompt_cache_key="momcozy-status-advice-v1",
        )
    except Exception:
        return None

    return _parse_advice_response(response)


def _parse_advice_response(response: object) -> dict[str, str] | None:
    text = _response_text(response)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    lactation_advice = _clean_advice(parsed.get("lactation_advice"))
    feeding_advice = _clean_advice(parsed.get("feeding_advice"))
    if not lactation_advice and not feeding_advice:
        return None
    return {
        "lactation_advice": lactation_advice,
        "feeding_advice": feeding_advice,
    }


def _clean_advice(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:50]


def _response_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    output = response.get("output", []) if isinstance(response, dict) else getattr(response, "output", [])
    if not isinstance(output, list):
        return ""

    parts: list[str] = []
    for item in output:
        item_type = _item_value(item, "type")
        if item_type == "message":
            content = _item_value(item, "content") or []
            if not isinstance(content, list):
                continue
            for content_item in content:
                if _item_value(content_item, "type") in {"output_text", "text"}:
                    text = _item_value(content_item, "text")
                    if isinstance(text, str):
                        parts.append(text)
        elif item_type == "output_text":
            text = _item_value(item, "text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _item_value(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _days_since(date_text: Any) -> int | None:
    token = str(date_text or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token).date()
    except Exception:
        return None
    return max(0, (datetime.now().date() - parsed).days)


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(float(value))
    except Exception:
        return None
