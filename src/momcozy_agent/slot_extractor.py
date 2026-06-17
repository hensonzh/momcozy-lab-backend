from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .contexts import HOSPITAL_BAG_SLOT_FIELDS

DEFAULT_SLOT_EXTRACTOR_MODEL = "gpt-5.4-mini"
SLOT_EXTRACTOR_VERSION = "birth_prep_slots_async_v1"


@dataclass(frozen=True)
class BirthPrepSlotExtractionRequest:
    user_message: str
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
    field_list = ", ".join(HOSPITAL_BAG_SLOT_FIELDS)
    return (
        "You extract MomCozy birth-prep session slots from the latest user message. "
        "Return JSON only, shaped as {\"slots\":[{\"field_id\":\"...\",\"value\":...,\"evidence\":\"...\",\"confidence\":0.0-1.0}]}. "
        "Only extract information the user explicitly states in the latest user message; do not infer from the assistant question, "
        "do not copy long prose into short fields, and do not invent missing values. "
        f"Allowed field_id values: {field_list}. "
        "If no explicit slot is present, return {\"slots\":[]}. "
        "For city_or_country, output only the city/country phrase, never a full sentence. "
        "For due_date_or_week, keep pregnancy week or due date exactly and compactly. "
        "Treat user-stated updates as the latest value."
    )


def _request_payload(request: BirthPrepSlotExtractionRequest) -> dict[str, Any]:
    return {
        "latest_user_message": request.user_message,
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
