from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs

DEFAULT_CHAT_URL = "/ibclc-chat.html"
DEFAULT_SPECIALTIES = [
    "亲喂含乳与姿势调整",
    "吸奶计划与排乳效率",
    "乳头疼痛与堵奶支持",
    "奶量管理与宝宝摄入观察",
]
DEFAULT_HELP_TOPICS = [
    {
        "title": "乳房不适或堵奶反复",
        "detail": "一起看疼痛位置、红肿范围、硬块变化和排乳方式。",
    },
    {
        "title": "乳头疼痛或含乳困难",
        "detail": "结合乳头状态、宝宝含乳和喂奶姿势做调整建议。",
    },
    {
        "title": "奶量波动或追奶节奏",
        "detail": "根据亲喂、吸奶记录和宝宝摄入信号梳理计划。",
    },
    {
        "title": "吸奶器排乳效率",
        "detail": "排查法兰尺寸、吸力档位、吸奶时长和舒适度。",
    },
]
DEFAULT_PREP_ITEMS = [
    "体温、发热持续时间，以及是否有寒战。",
    "疼痛位置、红肿范围、有无硬块或加重。",
    "最近 24 小时亲喂/吸奶次数和大概奶量。",
    "宝宝尿布、精神状态和吃奶表现。",
]
DEFAULT_BOUNDARY_NOTE = "如果高烧、寒战、红肿快速扩大或疼痛明显加重，请优先联系医生；IBCLC 主要帮助你看含乳、排乳、吸奶和喂养方式。"


def create_ibclc_consult_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    consultant = {
        "name": _text(args.get("consultant_name"), "Emily Chen"),
        "credentials": _text(args.get("consultant_credentials"), "IBCLC 国际认证哺乳顾问"),
        "experience": _text(args.get("consultant_experience"), "多年产后哺乳支持经验"),
        "bio": _text(
            args.get("consultant_bio"),
            "国际认证哺乳顾问，专注产后亲喂、吸奶计划、乳头疼痛、堵奶和奶量管理支持。",
        ),
        "specialties": _text_list(args.get("specialties"), DEFAULT_SPECIALTIES),
    }
    card = {
        "card_type": "ibclc_consult_card",
        "schema_version": "1.1",
        "title": "IBCLC 在线咨询",
        "subtitle": "把本轮已经描述的情况带给顾问，继续看喂养和排乳方式。",
        "consultant": consultant,
        "help_topics": _topic_list(args.get("help_topics"), DEFAULT_HELP_TOPICS),
        "prep_items": _text_list(args.get("prep_items"), DEFAULT_PREP_ITEMS),
        "boundary_note": _text(args.get("boundary_note"), DEFAULT_BOUNDARY_NOTE),
        "chat": {
            "url": _text(args.get("chat_url"), DEFAULT_CHAT_URL),
            "label": _text(args.get("chat_label"), "咨询 IBCLC"),
            "hint": "会带上本轮已描述的情况，方便顾问快速接手。",
        },
    }
    return {
        "tool_name": "ibclc_consult_card_create",
        "status": "ibclc_consult_card_created",
        "card": card,
    }


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _text_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    items = [_text(item) for item in value]
    items = [item for item in items if item]
    return items or list(fallback)


def _topic_list(value: Any, fallback: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return [dict(item) for item in fallback]

    topics: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            title = _text(item.get("title"))
            detail = _text(item.get("detail"))
        else:
            title = _text(item)
            detail = ""
        if title or detail:
            topics.append({"title": title or detail, "detail": detail})
    return topics or [dict(item) for item in fallback]
