from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .contexts import HOSPITAL_BAG_SLOT_FIELDS, PROFILE_SLOT_FIELDS

DEFAULT_SLOT_EXTRACTOR_MODEL = "gpt-5.4-mini"
SLOT_EXTRACTOR_VERSION = "birth_prep_slots_async_v1"


@dataclass(frozen=True)
class BirthPrepSlotExtractionRequest:
    user_message: str
    recent_user_messages: list[str] = field(default_factory=list)
    previous_assistant_message: str = ""
    current_slots: dict[str, Any] = field(default_factory=dict)
    loaded_skill_ids: list[str] = field(default_factory=list)
    locale: str = ""
    timezone: str = ""
    message_sent_at: str = ""


class BirthPrepSlotExtractor:
    def __init__(self, client: Any, *, model: str | None = None, enabled: bool | None = None) -> None:
        self.client = client
        self.model = model or os.getenv("MOMCOZY_SLOT_EXTRACTOR_MODEL") or DEFAULT_SLOT_EXTRACTOR_MODEL
        if enabled is None:
            enabled = os.getenv("MOMCOZY_SLOT_EXTRACTOR_DISABLED", "").strip().lower() not in {"1", "true", "yes"}
        self.enabled = enabled

    def extract(self, request: BirthPrepSlotExtractionRequest) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user_message = str(request.user_message or "").strip()
        if not user_message or "confirmed_form_data:" in user_message:
            return []

        response = self.client.responses.create(
            model=self.model,
            instructions=_slot_extractor_instructions(),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(_request_payload(request), ensure_ascii=False),
                        }
                    ],
                }
            ],
            store=False,
            text={"format": {"type": "text"}},
        )
        return parse_slot_extractor_response_text(_response_text(response))


def parse_slot_extractor_response_text(text: str) -> list[dict[str, Any]]:
    data = _loads_json_object(text)
    if isinstance(data, dict):
        slots = data.get("slots")
        if isinstance(slots, list):
            return [slot for slot in slots if isinstance(slot, dict)]
        if data.get("field_id") or data.get("field"):
            return [data]
    if isinstance(data, list):
        return [slot for slot in data if isinstance(slot, dict)]
    return []


def _slot_extractor_instructions() -> str:
    profile_field_list = ", ".join(PROFILE_SLOT_FIELDS)
    birth_prep_field_list = ", ".join(HOSPITAL_BAG_SLOT_FIELDS)
    field_list = ", ".join(dict.fromkeys((*PROFILE_SLOT_FIELDS, *HOSPITAL_BAG_SLOT_FIELDS)))
    return (
        "在进行 MomCozy 备产会话信息抽取时，必须综合最近历史上下文 + 当前用户最新输入进行联合判断，不得仅依赖单条消息。\n\n"
        "抽取过程中必须结合 field_id 的语义进行校验，避免字段错配（例如不能将人名、关系名等误判为 city_or_country）。\n\n"
        "规则要求如下：\n\n"
        "- 必须基于最近上下文与当前用户输入共同提取信息。\n"
        "- 严格按照 field_id 的语义进行归类，禁止“关键词匹配式”填充。\n"
        "- city_or_country 仅允许填写明确的城市或国家名称，禁止填入人名、机构名或关系描述。\n"
        "- display_name 仅允许填写用户希望被如何称呼的名字/称呼，禁止填入城市、医院、关系名或长句。\n"
        "- age 仅允许填写用户明确提供的当前年龄，不要把孕周、宝宝月龄或住院天数填入 age。\n"
        "- value 必须与字段语义一致，不得跨字段混用或泛化。\n"
        "- 若存在冲突信息，以最新用户输入为准。\n"
        "- 若无法确定字段归属，则不填该字段，不得猜测或补全。\n"
        "- 只抽取用户在最近上下文或当前最新输入中明确表达的信息；不要复制长段文本到短字段。\n"
        "- 必须只返回 JSON，不要输出解释、Markdown 或代码块。\n"
        f"- profile slot field_id values: {profile_field_list}。\n"
        f"- birth-prep slot field_id values: {birth_prep_field_list}。\n"
        f"- Allowed field_id values: {field_list}。\n"
        "- For due_date_or_week, keep pregnancy week or due date exactly and compactly。\n\n"
        "输出格式仍保持：\n\n"
        "{\"slots\":[{\"field_id\":\"...\",\"value\":...,\"evidence\":\"...\",\"confidence\":0.0-1.0}]}\n\n"
        "如果没有明确可归属字段，返回 {\"slots\":[]}。"
    )


def _request_payload(request: BirthPrepSlotExtractionRequest) -> dict[str, Any]:
    return {
        "latest_user_message": request.user_message,
        "recent_user_messages": request.recent_user_messages or ([request.user_message] if request.user_message else []),
        "previous_assistant_message": request.previous_assistant_message,
        "current_slots": request.current_slots,
        "loaded_skill_ids": request.loaded_skill_ids,
        "locale": request.locale,
        "timezone": request.timezone,
        "message_sent_at": request.message_sent_at,
    }


def _loads_json_object(text: str) -> Any:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = _strip_code_fence(cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text
    if isinstance(response, dict):
        value = response.get("output_text")
        if isinstance(value, str) and value:
            return value
        output = response.get("output")
    else:
        output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
