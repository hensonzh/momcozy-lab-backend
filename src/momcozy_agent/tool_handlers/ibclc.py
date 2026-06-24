from __future__ import annotations

import re
from typing import Any

from ..types import RuntimeInputs

DEFAULT_CHAT_URL = "/ibclc-chat.html"
DEFAULT_CONSULTANT_BIO = "拥有 8 年产后哺乳支持经验，核心擅长含乳评估、有效吸吮与母乳移出观察。可结合宝宝尿布、体重和吃奶表现判断摄入信号，并围绕亲喂姿势、乳头疼痛、堵奶/乳房不适、吸奶器使用和排乳计划给出个性化调整建议。"
DEFAULT_RECOMMENDATION_TOPIC = "含乳、排乳、亲喂/吸奶效果和乳房不适"
DEFAULT_SERVICE_LOCATION_NOTE = "她也恰好和你同城，后面有必要也可以上门服务。"
_CONFIRMATION_QUESTION = "要我帮你打开 IBCLC 在线咨询入口吗？"


def create_ibclc_consult_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    confirmation = _ibclc_consult_creation_confirmation(inputs)
    if not confirmation["allowed"]:
        return {
            "tool_name": "ibclc_consult_card_create",
            "status": "ibclc_consult_blocked",
            "reason": confirmation["reason"],
            "requires_user_confirmation": True,
            "confirmation_question": _CONFIRMATION_QUESTION,
        }

    consultant = {
        "name": _text(args.get("consultant_name"), "Emily Chen"),
        "credentials": _text(args.get("consultant_credentials"), "IBCLC 国际认证哺乳顾问"),
        "experience": _text(args.get("consultant_experience"), "8 年产后哺乳支持经验"),
        "bio": _clean_consultant_bio(
            _text(
                args.get("consultant_bio"),
                DEFAULT_CONSULTANT_BIO,
            )
        ),
    }
    card = {
        "card_type": "ibclc_consult_card",
        "schema_version": "1.1",
        "title": "IBCLC 在线咨询",
        "consultant": consultant,
        "recommendation_reason": _consultant_recommendation_reason(consultant, args, inputs),
        "chat": {
            "url": _text(args.get("chat_url"), DEFAULT_CHAT_URL),
            "label": _text(args.get("chat_label"), "咨询 IBCLC"),
            "note": "启动咨询后，会自动将你的问题同步给顾问",
        },
    }
    return {
        "tool_name": "ibclc_consult_card_create",
        "status": "ibclc_consult_card_created",
        "card": card,
    }


def ibclc_consult_creation_allowed(inputs: RuntimeInputs) -> bool:
    return _ibclc_consult_creation_confirmation(inputs).get("allowed") is True


def _ibclc_consult_creation_confirmation(inputs: RuntimeInputs) -> dict[str, Any]:
    user_message = _normalize_confirmation_text(inputs.get("user_message"))
    if _is_explicit_ibclc_request(user_message):
        return {"allowed": True, "source": "explicit_user_request"}
    if _is_short_affirmation(user_message):
        previous_message = _normalize_confirmation_text(
            inputs.get("previous_assistant_message") or inputs.get("_last_assistant_message")
        )
        if _previous_assistant_offered_ibclc(previous_message):
            return {"allowed": True, "source": "confirmed_previous_offer"}
        return {"allowed": False, "reason": "short_confirmation_without_ibclc_offer"}
    return {"allowed": False, "reason": "missing_explicit_ibclc_request"}


def _normalize_confirmation_text(value: Any) -> str:
    text = _text(value).lower()
    return re.sub(r"\s+", "", text)


def _is_explicit_ibclc_request(text: str) -> bool:
    if not text:
        return False
    if _has_negative_ibclc_intent(text):
        return False
    has_ibclc_subject = "ibclc" in text or "哺乳顾问" in text or "泌乳顾问" in text
    has_request_action = any(
        token in text
        for token in (
            "找",
            "推荐",
            "打开",
            "接通",
            "咨询",
            "需要",
            "想要",
            "我要",
            "帮我",
            "给我",
            "安排",
            "入口",
            "同意",
        )
    )
    if has_ibclc_subject and has_request_action:
        return True
    if ("真人哺乳咨询" in text or "人工哺乳咨询" in text) and has_request_action:
        return True
    if "打开咨询入口" in text or "打开在线咨询" in text or "启动咨询" in text:
        return True
    if "顾问咨询" in text and any(token in text for token in ("打开", "启动", "接通", "找", "推荐", "入口", "帮我", "需要", "想要", "我要")):
        return True
    if "同意推荐" in text or "同意你推荐" in text:
        return True
    if ("找" in text or "推荐" in text or "接通" in text or "打开" in text) and "咨询入口" in text:
        return True
    return False


def _has_negative_ibclc_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "不要ibclc",
            "不用ibclc",
            "不需要ibclc",
            "不找ibclc",
            "不用找ibclc",
            "先不找ibclc",
            "先不用找ibclc",
            "暂时不找ibclc",
            "暂时不用找ibclc",
            "不要哺乳顾问",
            "不用哺乳顾问",
            "不需要哺乳顾问",
            "不找哺乳顾问",
            "不用找哺乳顾问",
            "不要泌乳顾问",
            "不用泌乳顾问",
            "不需要泌乳顾问",
            "不找泌乳顾问",
            "不用找泌乳顾问",
            "不要打开咨询入口",
            "不用打开咨询入口",
            "不打开咨询入口",
            "别打开咨询入口",
            "先不打开咨询入口",
            "暂时不打开咨询入口",
        )
    )


def _is_short_affirmation(text: str) -> bool:
    if not text:
        return False
    normalized = re.sub(r"[。！？!?,，、~～….\-_\s]", "", text)
    return normalized in {
        "ok",
        "okay",
        "好",
        "好的",
        "好啊",
        "可以",
        "行",
        "可以的",
        "要",
        "需要",
        "同意",
        "打开吧",
        "帮我打开",
        "推荐一下",
        "开始吧",
        "嗯",
        "嗯嗯",
    }


def _previous_assistant_offered_ibclc(text: str) -> bool:
    if not text:
        return False
    has_ibclc_subject = "ibclc" in text or "哺乳顾问" in text or "泌乳顾问" in text
    if not has_ibclc_subject:
        return False
    has_offer = any(
        token in text
        for token in (
            "需要我帮你推荐",
            "帮你推荐",
            "要我帮你",
            "我可以帮你",
            "是否需要",
            "要不要",
            "可以打开",
            "打开咨询入口",
            "在线咨询入口",
            "咨询入口",
        )
    )
    return has_offer


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _clean_consultant_bio(text: str) -> str:
    cleaned = text.strip()
    removable_prefixes = (
        "IBCLC 国际认证哺乳顾问",
        "IBCLC 国际认证泌乳顾问",
        "国际认证哺乳顾问",
        "国际认证泌乳顾问",
    )
    for prefix in removable_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip(" ，,、。")
            break
    return cleaned or DEFAULT_CONSULTANT_BIO


def _consultant_recommendation_reason(consultant: dict[str, str], args: dict[str, Any], inputs: RuntimeInputs) -> str:
    name = _text(consultant.get("name"), "这位顾问")
    topic = _recommendation_topic(args, inputs)
    issue_phrase = _recommendation_issue_phrase(args, inputs)
    service_note = _text(args.get("service_location_note"), DEFAULT_SERVICE_LOCATION_NOTE)
    issue_part = f"，正好对应你刚才提到的{issue_phrase}" if issue_phrase else ""
    return f"我推荐 {name}，是因为她擅长{topic}{issue_part}；{_ensure_sentence(service_note)}"


def _recommendation_topic(args: dict[str, Any], inputs: RuntimeInputs) -> str:
    explicit = _text(args.get("recommendation_topic"))
    if explicit:
        return explicit.rstrip("。；;，, ")
    text = _issue_context_text(args, inputs)
    topics: list[str] = []
    if _contains_any(text, ("含乳", "吸不住", "乳头", "亲喂", "姿势", "衔乳", "浅含")):
        topics.append("含乳评估、亲喂姿势和乳头疼痛处理")
    if _contains_any(text, ("堵奶", "硬块", "胀痛", "涨奶", "乳房", "排不空", "红肿", "刺痛")):
        topics.append("堵奶/硬块、乳房不适和排乳方式调整")
    if _contains_any(text, ("吸奶", "泵奶", "吸力", "排乳", "排空", "奶量下降", "奶少", "追奶")):
        topics.append("吸奶器使用、排乳节奏和奶量变化")
    if _contains_any(text, ("宝宝", "尿布", "体重", "精神", "摄入", "吃不饱", "吃奶", "补奶")):
        topics.append("宝宝摄入判断、尿布/体重信号和喂养安排")
    if _contains_any(text, ("混合", "瓶喂", "配方", "背奶", "返工", "上班")):
        topics.append("混合喂养、瓶喂衔接和背奶安排")
    if not topics:
        return DEFAULT_RECOMMENDATION_TOPIC
    return "、".join(_unique_texts(topics)[:2])


def _recommendation_issue_phrase(args: dict[str, Any], inputs: RuntimeInputs) -> str:
    explicit = _text(args.get("issue_summary"))
    if explicit:
        return _trim_issue_phrase(explicit)
    text = _issue_context_text(args, inputs)
    if _contains_any(text, ("含乳", "吸不住", "亲喂", "姿势", "乳头", "衔乳", "浅含")):
        return "含乳或亲喂不顺"
    if _contains_any(text, ("堵奶", "硬块", "胀痛", "涨奶", "乳房", "排不空")):
        return "乳房不适或堵奶反复"
    if _contains_any(text, ("吸奶", "泵奶", "吸力", "排乳", "排空", "奶量下降", "奶少", "追奶")):
        return "吸奶节奏或奶量变化"
    if _contains_any(text, ("宝宝", "尿布", "体重", "精神", "摄入", "吃不饱", "吃奶", "补奶")):
        return "宝宝摄入不够安心"
    if _contains_any(text, ("混合", "瓶喂", "配方", "背奶", "返工", "上班")):
        return "喂养衔接安排"
    return ""


def _issue_context_text(args: dict[str, Any], inputs: RuntimeInputs) -> str:
    values = (
        args.get("issue_summary"),
        args.get("recommendation_topic"),
        inputs.get("user_message"),
    )
    return " ".join(_text(value) for value in values if _text(value))


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _unique_texts(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _trim_issue_phrase(value: str) -> str:
    text = value.strip().strip("。；;，, ")
    if len(text) <= 20:
        return text
    return text[:19].rstrip("。；;，, ") + "…"


def _ensure_sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return text if text[-1] in "。！？!?" else text + "。"
