from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs

DEFAULT_CHAT_URL = "/ibclc-chat.html"
DEFAULT_CONSULTANT_BIO = "拥有 8 年产后哺乳支持经验，核心擅长含乳评估、有效吸吮与母乳移出观察。可结合宝宝尿布、体重和吃奶表现判断摄入信号，并围绕亲喂姿势、乳头疼痛、堵奶/乳房不适、吸奶器使用和排乳计划给出个性化调整建议。"


def create_ibclc_consult_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
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
