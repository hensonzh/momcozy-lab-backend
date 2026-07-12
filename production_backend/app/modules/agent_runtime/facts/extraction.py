from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ..sdk import AgentModelRunner, SdkNodeRequest
from .catalog import FACT_CATALOG_VERSION, FACT_FIELD_BY_KEY, extractor_catalog_payload, normalize_fact_value
from .repository import AgentFactRepository
from .service import AgentFactService
from .types import FactApplyResult, FactInput


FACT_EXTRACTOR_VERSION = "turn-fact-extractor-v1"
FACT_EXTRACTOR_INSTRUCTIONS = """
你是 MomCozy 的后台结构化信息提取器，不参与用户回复，也不能调用工具。

只从 source_user_message 中提取当前用户本人明确陈述、且属于 field_catalog 的事实。recent_dialogue 只用于理解指代和纠正，不能把 assistant 的话当作事实来源。

规则：
- 区分当前用户、朋友、家人、上一胎和本次妊娠；只记录当前用户或本次妊娠。
- 用户在同一句中纠正旧值时，只输出纠正后的值。
- “可能、也许、医生怀疑、还没确认”等输出 uncertain；推测信息输出 inferred。
- 没有明确事实时返回 {"facts":[]}。
- value 必须符合目录类型和枚举；不要创造目录外字段。
- evidence 只摘录 source_user_message 中支持该事实的最短片段。
- 最多输出 12 条，只返回符合 schema 的 JSON。
""".strip()
FACT_EXTRACTOR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "turn_user_facts",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts"],
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["fact_key", "operation", "value", "certainty", "evidence"],
                    "properties": {
                        "fact_key": {"type": "string", "enum": sorted(FACT_FIELD_BY_KEY)},
                        "operation": {"type": "string", "enum": ["set"]},
                        "value": {
                            "anyOf": [
                                {"type": "string", "maxLength": 1000},
                                {"type": "integer"},
                                {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 500}},
                            ]
                        },
                        "certainty": {"type": "string", "enum": ["explicit", "uncertain", "inferred"]},
                        "evidence": {"type": "string", "maxLength": 500},
                    },
                },
            }
        },
    },
}


@dataclass(frozen=True)
class FactExtractionContext:
    owner_user_id: UUID
    run_id: UUID
    source_message_id: UUID
    source_text: str
    recent_dialogue: tuple[tuple[str, str], ...]
    existing_facts: dict[str, Any]
    observed_at: datetime


class AgentFactExtractor:
    def __init__(self, *, model_runner: AgentModelRunner, extractor_version: str = FACT_EXTRACTOR_VERSION) -> None:
        self.model_runner = model_runner
        self.extractor_version = extractor_version

    async def extract(self, *, context: FactExtractionContext) -> list[FactInput]:
        result = await self.model_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(context.run_id),
                thread_id=f"fact-extraction:{context.owner_user_id}",
                actor_user_id=str(context.owner_user_id),
                instructions=FACT_EXTRACTOR_INSTRUCTIONS,
                model_input=[{"role": "user", "content": json.dumps(_extractor_input(context), ensure_ascii=False, sort_keys=True)}],
                prompt_version=self.extractor_version,
                trace_id=f"fact-extraction:{context.run_id}",
                service_skill_id="turn-fact-extractor",
                response_text_format=FACT_EXTRACTOR_RESPONSE_FORMAT,
            )
        )
        return _normalize_inputs(_json_object(result.final_text).get("facts"), source_text=context.source_text)


class AgentFactCaptureService:
    def __init__(
        self,
        *,
        repository: AgentFactRepository,
        fact_service: AgentFactService,
        extractor: AgentFactExtractor,
        model: str,
        extractor_version: str = FACT_EXTRACTOR_VERSION,
    ) -> None:
        self.repository = repository
        self.fact_service = fact_service
        self.extractor = extractor
        self.model = model
        self.extractor_version = extractor_version

    async def capture(self, *, context: FactExtractionContext) -> FactApplyResult | None:
        extraction = await self.repository.begin_extraction(
            owner_user_id=context.owner_user_id,
            source_message_id=context.source_message_id,
            source_run_id=context.run_id,
            catalog_version=FACT_CATALOG_VERSION,
            extractor_version=self.extractor_version,
            model=self.model,
        )
        if extraction is None:
            return None
        try:
            inputs = await self.extractor.extract(context=context)
            result = await self.fact_service.apply_inputs(
                owner_user_id=context.owner_user_id,
                inputs=inputs,
                source_type="conversation",
                source_id=str(context.source_message_id),
                observed_at=context.observed_at,
            )
            await self.repository.complete_extraction(
                extraction=extraction,
                extracted_count=len(inputs),
                applied_count=result.applied_count,
            )
            return result
        except Exception as exc:
            await self.repository.fail_extraction(extraction=extraction, error_code=type(exc).__name__)
            raise


def _extractor_input(context: FactExtractionContext) -> dict[str, Any]:
    return {
        "field_catalog_version": FACT_CATALOG_VERSION,
        "observed_at": context.observed_at.isoformat(),
        "field_catalog": extractor_catalog_payload(),
        "existing_facts": context.existing_facts,
        "recent_dialogue": [{"role": role, "text": text} for role, text in context.recent_dialogue[-5:]],
        "source_user_message": context.source_text,
    }


def _normalize_inputs(value: Any, *, source_text: str) -> list[FactInput]:
    if not isinstance(value, list):
        return []
    normalized: dict[str, FactInput] = {}
    for item in value[:12]:
        if not isinstance(item, dict) or item.get("operation") != "set":
            continue
        fact_key = str(item.get("fact_key") or "").strip()
        certainty = str(item.get("certainty") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if (
            fact_key not in FACT_FIELD_BY_KEY
            or certainty not in {"explicit", "uncertain", "inferred"}
            or not evidence
            or evidence not in source_text
        ):
            continue
        try:
            fact_value = normalize_fact_value(fact_key, item.get("value"))
        except (TypeError, ValueError):
            continue
        if certainty != "explicit":
            continue
        normalized[fact_key] = FactInput(fact_key, fact_value, certainty, evidence[:500])
    return list(normalized.values())


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
