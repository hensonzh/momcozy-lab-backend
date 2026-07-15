from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ..sdk import AgentModelRunner, SdkNodeRequest
from .catalog import (
    FACT_CATALOG_VERSION,
    FACT_FIELD_BY_KEY,
    candidate_value_contains_restricted_content,
    candidate_value_matches_safe_shape,
    expected_conversation_subject,
    extractor_catalog_payload,
    normalize_fact_value,
)
from .types import FactInput


FACT_EXTRACTOR_VERSION = "turn-fact-extractor-v2"
FACT_EXTRACTOR_INSTRUCTIONS = """
你是 MomCozy 的后台结构化信息提取器，不参与用户回复，也不能调用工具。

只从 source_user_message 中提取当前用户本人明确陈述、属于 field_catalog 且允许作为表单预填候选的信息。recent_dialogue 只用于理解指代和纠正，不能把 assistant 的话当作事实来源。

规则：
- profile.* 和 birth_plan.* 的 subject 必须是 self；pregnancy.* 的 subject 必须是 current_pregnancy。
- 朋友、家人、同事、其他用户、本次之外的既往妊娠一律标为 other 或 previous_pregnancy。
- 用户在同一句中纠正旧值时，只输出纠正后的值。
- “可能、也许、医生怀疑、还没确认”等输出 uncertain；推测信息输出 inferred。
- 诊断、疾病、用药、过敏等 restricted_health 内容不能改写到其他自由文本字段中。
- 没有明确事实时返回 {"facts":[]}。
- value 必须符合目录类型和枚举；不要创造目录外字段。
- evidence 只摘录 source_user_message 中支持该事实的最短片段。
- 最多输出 12 条，只返回符合 schema 的 JSON。
""".strip()
FACT_EXTRACTOR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "turn_user_fact_candidates",
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
                    "required": ["fact_key", "operation", "value", "subject", "certainty", "evidence"],
                    "properties": {
                        "fact_key": {
                            "type": "string",
                            "enum": sorted(
                                key for key, field in FACT_FIELD_BY_KEY.items() if field.candidate_eligible
                            ),
                        },
                        "operation": {"type": "string", "enum": ["set"]},
                        "value": {
                            "anyOf": [
                                {"type": "string", "maxLength": 1000},
                                {"type": "integer"},
                                {
                                    "type": "array",
                                    "maxItems": 12,
                                    "items": {"type": "string", "maxLength": 500},
                                },
                            ]
                        },
                        "subject": {
                            "type": "string",
                            "enum": ["self", "current_pregnancy", "other", "previous_pregnancy", "unknown"],
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
    observed_at: datetime
    trace_id: str = ""


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
                model_input=[
                    {
                        "role": "user",
                        "content": json.dumps(_extractor_input(context), ensure_ascii=False, sort_keys=True),
                    }
                ],
                prompt_version=self.extractor_version,
                trace_id=context.trace_id or f"fact-extraction:{context.run_id}",
                service_skill_id="turn-fact-extractor",
                response_text_format=FACT_EXTRACTOR_RESPONSE_FORMAT,
            )
        )
        return _normalize_inputs(_json_object(result.final_text).get("facts"), source_text=context.source_text)


def fact_inputs_to_candidate_payload(inputs: list[FactInput]) -> list[dict[str, Any]]:
    return [
        {
            "fact_key": item.fact_key,
            "value": item.value,
            "subject": item.subject,
        }
        for item in inputs
    ]


def candidate_payload_to_inputs(candidates: list[dict[str, Any]]) -> list[FactInput]:
    return [
        FactInput(
            fact_key=str(item.get("fact_key") or ""),
            value=item.get("value"),
            certainty="explicit",
            subject=str(item.get("subject") or ""),
        )
        for item in candidates
        if isinstance(item, dict)
    ]


def _extractor_input(context: FactExtractionContext) -> dict[str, Any]:
    return {
        "field_catalog_version": FACT_CATALOG_VERSION,
        "observed_at": context.observed_at.isoformat(),
        "field_catalog": extractor_catalog_payload(),
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
        subject = str(item.get("subject") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        field = FACT_FIELD_BY_KEY.get(fact_key)
        if (
            field is None
            or not field.candidate_eligible
            or certainty != "explicit"
            or subject != expected_conversation_subject(fact_key)
            or not evidence
            or evidence not in source_text
            or _source_names_other_subject(source_text=source_text, evidence=evidence, fact_key=fact_key)
            or _source_is_uncertain(source_text=source_text, evidence=evidence)
        ):
            continue
        try:
            fact_value = normalize_fact_value(fact_key, item.get("value"))
        except (TypeError, ValueError):
            continue
        if (
            candidate_value_contains_restricted_content(fact_value)
            or not candidate_value_matches_safe_shape(fact_key=fact_key, value=fact_value)
            or not _value_is_grounded(value=fact_value, evidence=evidence)
            or not _source_affirms_expected_subject(
                source_text=source_text,
                evidence=evidence,
                fact_key=fact_key,
                value=fact_value,
            )
        ):
            continue
        normalized[fact_key] = FactInput(
            fact_key=fact_key,
            value=fact_value,
            certainty=certainty,
            evidence=evidence[:500],
            subject=subject,
        )
    return list(normalized.values())


def _source_names_other_subject(*, source_text: str, evidence: str, fact_key: str) -> bool:
    clause = _source_clause_for_evidence(source_text=source_text, evidence=evidence)
    if clause is None:
        return True
    normalized = "".join(clause.lower().split())
    previous_pregnancy = any(
        marker in normalized
        for marker in (
            "上一胎",
            "上次怀孕",
            "之前怀孕",
            "previouspregnancy",
            "lastpregnancy",
        )
    )
    if previous_pregnancy:
        return True
    third_party_markers = (
        "朋友",
        "同事",
        "闺蜜",
        "同学",
        "室友",
        "妹妹",
        "姐姐",
        "哥哥",
        "弟弟",
        "姐妹",
        "兄弟",
        "家人",
        "妈妈",
        "母亲",
        "爸爸",
        "父亲",
        "婆婆",
        "岳母",
        "女儿",
        "儿子",
        "丈夫",
        "老公",
        "妻子",
        "老婆",
        "对象",
        "伴侣",
        "表姐",
        "表妹",
        "表哥",
        "表弟",
        "堂姐",
        "堂妹",
        "堂哥",
        "堂弟",
        "亲戚",
        "邻居",
        "客户",
        "患者",
    )
    names_other = any(marker in normalized for marker in third_party_markers) or bool(
        re.search(
            r"\b(friend|coworker|co-worker|colleague|roommate|classmate|sister|brother|mother|mom|father|dad|"
            r"daughter|son|husband|wife|partner|spouse|cousin|relative|neighbor|neighbour|boss|client|patient|"
            r"she|her|hers|he|him|his|they|their)\b",
            clause.lower(),
        )
    )
    return names_other


def _source_is_uncertain(*, source_text: str, evidence: str) -> bool:
    clause = _source_clause_for_evidence(source_text=source_text, evidence=evidence)
    if clause is None:
        return True
    normalized = "".join(clause.lower().split())
    return any(
        marker in normalized
        for marker in (
            "可能",
            "也许",
            "或许",
            "大概",
            "估计",
            "应该",
            "好像",
            "似乎",
            "说不准",
            "不清楚",
            "怀疑",
            "不确定",
            "没确认",
            "还没确认",
            "未确认",
            "待确认",
            "maybe",
            "perhaps",
            "probably",
            "possibly",
            "notsure",
            "uncertain",
            "unconfirmed",
            "suspect",
            "might",
            "couldbe",
            "seems",
        )
    )


def _source_clause_for_evidence(*, source_text: str, evidence: str) -> str | None:
    indexes = [match.start() for match in re.finditer(re.escape(evidence), source_text)]
    if len(indexes) != 1:
        return None
    index = indexes[0]
    prefix = source_text[:index]
    suffix_start = index + len(evidence)
    suffix = source_text[suffix_start:]
    left_boundaries = list(re.finditer(r"[，。！？；,.!?;\n]", prefix))
    right_boundary = re.search(r"[，。！？；,.!?;\n]", suffix)
    left = left_boundaries[-1].end() if left_boundaries else 0
    right = suffix_start + right_boundary.start() if right_boundary is not None else len(source_text)
    clause = source_text[left:right].strip()
    return clause or None


def _value_is_grounded(*, value: Any, evidence: str) -> bool:
    normalized_evidence = "".join(str(evidence).lower().split())
    if isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return all("".join(item.lower().split()) in normalized_evidence for item in values)


def _source_affirms_expected_subject(
    *,
    source_text: str,
    evidence: str,
    fact_key: str,
    value: Any,
) -> bool:
    clause = _source_clause_for_evidence(source_text=source_text, evidence=evidence)
    if clause is None:
        return False
    compact = "".join(clause.lower().split())
    if fact_key == "profile.age":
        age = str(value)
        chinese_self_patterns = (
            f"我{age}岁",
            f"我是{age}岁",
            f"我今年{age}岁",
            f"我现在{age}岁",
            f"我目前{age}岁",
            f"我已经{age}岁",
            f"我刚满{age}岁",
            f"我的年龄是{age}",
            f"本人{age}岁",
        )
        corrected_self_age = compact.startswith(("我不是", "我刚才说错")) and f"是{age}" in compact
        return corrected_self_age or any(pattern in compact for pattern in chinese_self_patterns) or bool(
            re.search(rf"\b(?:i am|i'm|im|my age is)\s*{re.escape(age)}\b", clause.lower())
        )
    chinese_self_pregnancy = re.search(
        r"(?:^|[，。！？；])(?:"
        r"我(?:现在|目前|已经|正在)?(?:孕|怀孕|怀|这胎|这一胎|本次妊娠|计划|打算|想|希望|准备|决定|选择|授权|同意)"
        r"|我的(?:孕周|预产期|怀孕|妊娠|这胎|这一胎|分娩|生产|喂养|医生建议|计划|选择)"
        r"|我(?:是|不是)(?:第一胎)"
        r"|本人(?:现在|目前|已经|正在)?(?:孕|怀孕|怀|这胎|这一胎|本次妊娠|计划|打算|选择|授权|同意)"
        r")",
        compact,
    )
    english_self_pregnancy = re.search(
        r"\b(?:i am|i'm|im|i plan|i intend|i want|i hope|i choose|i authorize|i consent|"
        r"my pregnancy|my due date|my gestational age|my doctor)\b",
        clause.lower(),
    )
    return chinese_self_pregnancy is not None or english_self_pregnancy is not None


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "AgentFactExtractor",
    "FACT_EXTRACTOR_RESPONSE_FORMAT",
    "FACT_EXTRACTOR_VERSION",
    "FactExtractionContext",
    "candidate_payload_to_inputs",
    "fact_inputs_to_candidate_payload",
]
