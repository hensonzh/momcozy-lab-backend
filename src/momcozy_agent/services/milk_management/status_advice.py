from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from ...config import get_openai_client_options, load_project_env
from .. import data_store
from .assessment import evaluate_milk_status
from .feeding import estimate_breastfeeding_milk


SIMPLE_STATUS_ADVICE_PROMPT = """
你是 Momcozy 的每日泌乳建议助手。根据输入的生产日期、宝宝信息，以及近7天吸奶、亲喂和喂奶记录，生成一张给妈妈看的泌乳建议卡片。

要求：
- 只返回 JSON：{"summary":"...","status_prompt":"...","today_advice":"...","followup":"..."}
- summary：1 句话简单总结最近泌乳/排乳记录，不写标题。
- status_prompt：短状态，不超过10个字，例如“节奏稳定”“密切关注奶量中”“记录补充中”。
- today_advice：1 段今日建议，说明今天建议做什么、为什么，并自然提醒按日程吸奶；可以提到会提前15分钟提醒。
- followup：固定用“现在方便吗？我们聊一下奶量的问题。”
- 只关注泌乳、吸奶、亲喂排乳方向；不要生成单独的喂养建议段落。
- 不要用“AI分析/系统判断/根据模型”这类说法，不要像报告，不要长篇科普。
- 当有明确偏低或持续低于参考时，直接说出关键发现，但避免给妈妈贴“奶不够”的诊断标签。
- 如果记录提示可能影响宝宝摄入，只能提醒同步观察尿布、精神和体重；出现尿少、精神差、体重增长慢时建议联系儿科或 IBCLC。
- 不诊断、不承诺奶量一定够或不够，不替代医生、儿科医生或 IBCLC。
- 如果输入包含 analysis_normality：advice 必须和 result 方向一致。
- 当 result=true：先肯定当前节奏，再给保持建议，不要求增加、减少或明显改进。
- 当 result=false：必须根据 failed_metrics/reason 指出具体问题和可调整方向，不能只夸奖。
""".strip()

STATUS_ADVICE_MAX_CHARS = 180
STATUS_SUMMARY_MAX_CHARS = 90
STATUS_PROMPT_MAX_CHARS = 12
DEFAULT_LACTATION_FOLLOWUP = "现在方便吗？我们聊一下奶量的问题。"


def generate_status_advice(*, user_id: str, days: int = 7, normality: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Generate the daily lactation advice card payload."""

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
    generated = _enforce_attention_advice(generated, payload=payload, normality=normality)
    generated = _normalize_lactation_advice_payload(generated, payload=payload, normality=normality)
    if not generated.get("lactation_advice", "").strip():
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
            "lactation_advice": "这几天还没有看到稳定的吸奶或亲喂记录，先不用急着判断奶量好坏。现在最有帮助的是把每次吸奶、亲喂和大概时长记下来，连续记满几天后，我们就能更清楚地看出排乳节奏。",
            "feeding_advice": "宝宝这边也先别急着下结论，记录少的时候很容易把情况看得偏轻或偏重。今天开始先把每次亲喂、瓶喂、奶量和尿布情况记完整，后面会更容易判断宝宝摄入是否稳定。",
        }

    norm = normality if isinstance(normality, dict) else {}
    if norm.get("result") is True:
        return {
            "lactation_advice": "这几天的排乳节奏看起来没有明显异常，可以先安心一点。你不用为了追求更多奶量突然加很多次，先保持现在的吸奶和亲喂节奏，继续记录奶量、时长和舒适度就好。",
            "feeding_advice": "宝宝这几天的喂养次数整体还在参考范围内，先不需要把节奏改得很大。接下来继续看尿布、精神状态和体重变化，如果这些也稳定，就说明目前可以先按现在的方式观察。",
        }

    reason = str(norm.get("reason") or "").strip()
    if reason == "insufficient_minimum_valid_days":
        return {
            "lactation_advice": "现在能用来判断奶量趋势的有效记录还不够，不代表一定有问题。先把接下来几天的吸奶、亲喂和每次间隔记完整，尤其是清晨和夜间的排乳情况，这样后面判断会更可靠。",
            "feeding_advice": "喂养记录目前也还不够完整，所以先不要急着判断宝宝吃多吃少。今天开始把亲喂、瓶喂、奶量和尿布放在一起看，连续几天后，我们再判断是否需要调整节奏会更稳。",
        }

    failed_metrics = norm.get("failed_metrics") if isinstance(norm.get("failed_metrics"), list) else []
    failed_types = {
        str(item.get("type") or "").strip()
        for item in failed_metrics
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }
    if len(failed_types) > 1:
        return {
            "lactation_advice": "这几天不只是奶量数字偏低，吸奶和喂养节奏都值得认真看一下。今天把吸奶、亲喂和瓶喂分开记清楚，同时尽快整理一版追奶计划，会比零散加次数更有效。",
            "feeding_advice": "宝宝这边也需要同步关注，尤其是尿布、精神和体重变化。今天先把每次喂养和尿布记下来；如果尿明显少、精神差、吃奶变差或体重增长慢，建议及时联系儿科或 IBCLC。",
        }

    if "lactation" in failed_types or reason == "lactation_out_of_range":
        statuses = _failed_day_statuses(failed_metrics, metric_type="lactation")
        if "low" in statuses and "high" not in statuses:
            lactation_text = "近几天奶量低于参考，这个信号值得尽快处理，但不是要你一下子把自己逼得很累。今天可以先加1次清晨或夜间排乳，同时把每次奶量和间隔记清楚，接下来尽快整理一版追奶计划。"
        elif "high" in statuses and "low" not in statuses:
            lactation_text = "近几天奶量高于参考，先不用急着追求继续增加，重点是看身体舒不舒服。今天留意胀痛、硬块和排乳后的轻松程度，如果开始不适，可以先把节奏放稳，避免过度刺激。"
        else:
            lactation_text = "这几天奶量有波动，单看某一天容易让人紧张，也不一定代表趋势已经变差。先把记录时间固定一些，尤其看连续几天的总量、间隔和亲喂情况，再决定要不要调整会更稳。"
        return {
            "lactation_advice": lactation_text,
            "feeding_advice": "奶量偏低时，宝宝摄入也要一起看，不能只盯着吸出来的数字。今天同步观察尿布、精神状态和体重变化；如果尿少、精神差、吃奶变差或体重慢，建议联系儿科或 IBCLC。",
        }

    if "feeding" in failed_types or reason == "feeding_out_of_range":
        directions = _failed_feeding_directions(failed_metrics)
        if "low" in directions and "high" not in directions:
            feeding_text = "喂养次数偏少时，先别只靠感觉判断宝宝有没有吃够。今天把亲喂、瓶喂、每次奶量和尿布一起记下来；如果尿少、精神差、吃奶明显变弱或体重增长慢，建议联系儿科或 IBCLC。"
        elif "high" in directions and "low" not in directions:
            feeding_text = "喂养次数偏多不一定就是坏事，但需要看看每次是不是吃得有效。今天可以观察每次摄入量、宝宝吃完后的满足感和吐奶情况，如果只是频繁少量，可以再慢慢调整节奏。"
        else:
            feeding_text = "喂养频次有波动时，先不要急着把每一顿都改掉。接下来几天把亲喂、瓶喂、奶量和尿布连续记下来，我们看趋势会比看单次波动更可靠，也更不容易误判。"
        return {
            "lactation_advice": "排乳这边可以先保持当前节奏，不需要因为喂养次数波动就立刻大幅加减。今天重点是把吸奶、亲喂和宝宝需求放在一起看，等喂养记录更完整后再决定是否调整。",
            "feeding_advice": feeding_text,
        }

    return {
        "lactation_advice": "近几天记录有一些波动，先不用把它理解成确定的问题。你可以先把排乳时间和记录方式稳定下来，继续观察连续几天的奶量、间隔和身体舒适度，再决定是否需要进一步调整。",
        "feeding_advice": "宝宝喂养这边先把记录补完整会更有帮助。今天优先看每次喂养、尿布、精神状态和体重变化，如果这些信号都还稳，就先不要被单次记录牵着走。",
    }


def _enforce_attention_advice(
    advice: dict[str, Any],
    *,
    payload: dict[str, Any],
    normality: dict[str, Any] | None,
) -> dict[str, str]:
    cleaned = {
        "lactation_advice": _clean_advice(advice.get("lactation_advice")),
        "feeding_advice": _clean_advice(advice.get("feeding_advice")),
    }
    norm = normality if isinstance(normality, dict) else {}
    if norm.get("result") is True:
        return _humanize_advice_openings(cleaned)

    metrics = norm.get("metrics") if isinstance(norm.get("metrics"), list) else []
    low_days = [
        item
        for item in metrics
        if isinstance(item, dict)
        and (
            str(item.get("status") or "") == "low"
            or str(item.get("rule_hit") or "") == "percentile_below_p15"
        )
    ]
    persistent_low = len(low_days) >= max(3, min(5, len(metrics) or 0))

    failed_metrics = norm.get("failed_metrics") if isinstance(norm.get("failed_metrics"), list) else []
    failed_types = {
        str(item.get("type") or "").strip()
        for item in failed_metrics
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }
    feeding_directions = _failed_feeding_directions(failed_metrics)

    if persistent_low:
        avg_ml = _average_daily_milk(low_days)
        if avg_ml > 0:
            cleaned["lactation_advice"] = (
                f"近{len(low_days)}天预估日均约{_format_ml(avg_ml)}ml，已经连续低于参考，这个情况需要尽快处理，"
                "但不是说你做得不好，也不用一下子把自己压垮。今天先加1次清晨或夜间排乳，把间隔、奶量和身体感受记清楚；如果晚上实在累，就优先选最容易坚持的一次。我们先把节奏稳住，再尽快做一版追奶计划。"
            )
        else:
            cleaned["lactation_advice"] = "近几天奶量连续低于参考，这个信号值得尽快处理，但不是说你做得不好。今天先加1次清晨或夜间吸奶，把每次奶量、间隔和身体感受记清楚；如果晚上太累，就选最容易坚持的一次。我们先把节奏稳住，再尽快做一版追奶计划。"

        if "feeding" in failed_types or "low" in feeding_directions:
            cleaned["feeding_advice"] = "喂养次数也低于参考，所以宝宝摄入需要同步认真看。你先别一个人猜宝宝到底够不够，今天把亲喂、瓶喂、每次奶量和尿布一起记下来；如果尿少、精神差、吃奶变弱或体重增长慢，建议及时联系儿科或 IBCLC。我们先把最关键的信号握在手里，这样能更快把风险排清。"
        else:
            cleaned["feeding_advice"] = "奶量持续偏低时，宝宝不一定马上表现出来，但摄入信号要一起看。今天同步观察尿布、精神、吃奶状态和体重变化；如果有尿少、精神差、吃奶变弱或体重增长慢，建议及时联系儿科或 IBCLC。"
        return _humanize_advice_openings({key: _clean_advice(value) for key, value in cleaned.items()})

    if "feeding" in failed_types and "low" in feeding_directions:
        cleaned["feeding_advice"] = "喂养次数低于参考时，先别只凭感觉判断宝宝有没有吃够，也别把压力都放在自己身上。今天把亲喂、瓶喂、每次奶量和尿布补全；如果尿少、精神差、吃奶变弱或体重增长慢，建议联系儿科或 IBCLC。"

    if "lactation" in failed_types and not cleaned["lactation_advice"]:
        cleaned["lactation_advice"] = "奶量低于参考时，先稳住排乳节奏比临时乱加次数更重要。你不用一下子把安排塞满，今天先固定一个能做到的吸奶或亲喂安排，把每次奶量、时长、间隔和身体感受记下来，后面再看是否需要追奶计划。"

    return _humanize_advice_openings({key: _clean_advice(value) for key, value in cleaned.items()})


def _average_daily_milk(days: list[dict[str, Any]]) -> float:
    values = [_safe_float(item.get("estimated_daily_milk_ml")) for item in days if isinstance(item, dict)]
    values = [value for value in values if value > 0]
    return sum(values) / len(values) if values else 0.0


def _normalize_lactation_advice_payload(
    advice: dict[str, Any],
    *,
    payload: dict[str, Any],
    normality: dict[str, Any] | None,
) -> dict[str, str]:
    norm = normality if isinstance(normality, dict) else {}
    lactation = _clean_advice(advice.get("lactation_advice") or advice.get("today_advice"))
    summary = _clean_summary(advice.get("summary")) or _fallback_lactation_summary(payload=payload, normality=norm, lactation_advice=lactation)
    status_prompt = _clean_status_prompt(advice.get("status_prompt")) or _fallback_status_prompt(norm)
    today_advice = _clean_advice(advice.get("today_advice") or lactation)
    if not today_advice:
        today_advice = _fallback_today_advice(norm)
    followup = _clean_followup(advice.get("followup")) or DEFAULT_LACTATION_FOLLOWUP
    return {
        "summary": summary,
        "status_prompt": status_prompt,
        "today_advice": today_advice,
        "followup": followup,
        "lactation_advice": today_advice,
        "feeding_advice": "",
    }


def _fallback_lactation_summary(*, payload: dict[str, Any], normality: dict[str, Any], lactation_advice: str) -> str:
    if lactation_advice:
        first_sentence = _first_sentence(lactation_advice)
        if first_sentence:
            return _clean_summary(first_sentence)

    pumping = payload.get("pumping_summary") if isinstance(payload.get("pumping_summary"), dict) else {}
    total = _safe_float(pumping.get("total_effective_ml"))
    count = _int(pumping.get("record_count"), 0)
    if total > 0 or count > 0:
        return _clean_summary(f"近几天记录到 {count} 次排乳，累计约 {_format_ml(total)}ml。")

    reason = str(normality.get("reason") or "").strip()
    if reason == "insufficient_minimum_valid_days":
        return "最近可用记录还不够，先把排乳时间和奶量记完整。"
    return "今天先看排乳节奏和身体感受，不急着下结论。"


def _fallback_status_prompt(normality: dict[str, Any]) -> str:
    if not normality:
        return "继续观察中"
    if normality.get("result") is True or normality.get("lactation_normal") is True:
        return "节奏稳定"
    reason = str(normality.get("reason") or "").strip()
    if reason in {"insufficient_minimum_valid_days", "assessment_failed", "missing_user_id"}:
        return "记录补充中"
    metrics = normality.get("metrics") if isinstance(normality.get("metrics"), list) else []
    low_days = [
        item
        for item in metrics
        if isinstance(item, dict)
        and (
            str(item.get("status") or "") == "low"
            or str(item.get("rule_hit") or "") == "percentile_below_p15"
        )
    ]
    if len(low_days) >= max(3, min(5, len(metrics) or 0)):
        return "密切关注奶量中"
    if normality.get("lactation_normal") is False:
        return "留意奶量变化"
    return "继续观察中"


def _fallback_today_advice(normality: dict[str, Any]) -> str:
    if normality.get("result") is True or normality.get("lactation_normal") is True:
        return "今天先按原来的日程吸奶和亲喂，继续记录每次奶量、时长和舒适度；如果有计划任务，我会提前15分钟提醒你。"
    if str(normality.get("reason") or "") == "insufficient_minimum_valid_days":
        return "今天最重要的是把吸奶、亲喂时间和大概奶量记完整；记录够了，我们再判断是否需要调整。"
    return "今天先固定一个最容易坚持的排乳点，按日程完成吸奶；我会提前15分钟提醒你，避免临时忘记或间隔拉太长。"


def _first_sentence(text: str) -> str:
    token = str(text or "").strip()
    if not token:
        return ""
    for sep in ("。", "！", "？", ";", "；"):
        idx = token.find(sep)
        if 0 <= idx < 80:
            return token[: idx + 1]
    return token[:80]


def _clean_summary(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:STATUS_SUMMARY_MAX_CHARS]


def _clean_status_prompt(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:STATUS_PROMPT_MAX_CHARS]


def _clean_followup(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:40]


def _format_ml(value: float) -> str:
    rounded = round(float(value or 0), 1)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


def _humanize_advice_openings(advice: dict[str, str]) -> dict[str, str]:
    return {
        "lactation_advice": _ensure_human_opening(
            advice.get("lactation_advice"),
            prefixes=("嗨，", "我看到", "我发现", "我注意到"),
            fallback_prefix="嗨，我看到",
        ),
        "feeding_advice": _ensure_human_opening(
            advice.get("feeding_advice"),
            prefixes=("宝宝这边", "喂养这边", "我看到", "我发现", "我注意到"),
            fallback_prefix="宝宝这边我也想提醒一下，",
        ),
    }


def _ensure_human_opening(value: str | None, *, prefixes: tuple[str, ...], fallback_prefix: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(prefixes):
        return _clean_advice(text)
    return _clean_advice(f"{fallback_prefix}{text}")


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
        is_nursing = feed_type == data_store.FEED_TYPE_CODE_TO_TEXT[0]
        if amount <= 0 and not is_nursing:
            continue
        time_text = str(raw.get("feed_time") or "")
        date_key = time_text[:10]
        if is_nursing:
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
        client = OpenAI(**get_openai_client_options())
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
            prompt_cache_key="momcozy-status-advice-v4",
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
    summary = _clean_summary(parsed.get("summary"))
    status_prompt = _clean_status_prompt(parsed.get("status_prompt"))
    today_advice = _clean_advice(parsed.get("today_advice"))
    followup = _clean_followup(parsed.get("followup"))
    lactation_advice = _clean_advice(parsed.get("lactation_advice") or today_advice)
    feeding_advice = _clean_advice(parsed.get("feeding_advice"))
    if not lactation_advice and not feeding_advice:
        return None
    return {
        "summary": summary,
        "status_prompt": status_prompt,
        "today_advice": today_advice,
        "followup": followup,
        "lactation_advice": lactation_advice,
        "feeding_advice": feeding_advice,
    }


def _clean_advice(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:STATUS_ADVICE_MAX_CHARS]


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
