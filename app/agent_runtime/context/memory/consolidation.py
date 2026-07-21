from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.agent_runtime.providers import AgentModelRunner, SdkNodeRequest
from app.agent_runtime.runs.models import MEMORY_TYPES

from .service import AgentMemoryRepository, AgentMemoryService, validate_memory_write_policy


MEMORY_EXTRACTOR_VERSION = "memory-extractor-v1"
MAX_CONSOLIDATION_MESSAGES = 200
MAX_EXISTING_MEMORIES = 50
MIN_MEMORY_CONFIDENCE = 70
STALE_CONSOLIDATION_AFTER = timedelta(hours=1)
MEMORY_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,119}$")

MEMORY_EXTRACTOR_INSTRUCTIONS = """
你是 MomCozy 的夜间长期记忆提炼器。你只处理已经完成的用户与助手对话，不参与实时回复。

只提炼未来多轮对话仍有帮助、且由用户明确表达的稳定信息：
- user_preference：长期偏好；
- stable_care_preference：稳定的照护方式偏好，但不是诊断、症状、用药或宝宝健康事实；
- communication_preference：回复、提醒、称呼或表达方式偏好；
- recurring_constraint：反复存在的时间、可用性或执行约束。

规则：
- 只允许 user 消息作为 evidence_index；不要把 assistant 的推测或建议写成记忆。
- 不保存病情、症状、用药、检查结果、胎儿/婴儿健康、危机、财务、法律、身份凭据等敏感事实。
- 奶量记录、日记、计划、设备状态等业务数据属于业务表，不属于长期记忆。
- 短期话题、一次性请求、当前情绪和不确定推断不要写入。
- 新增或更新用 upsert；memory_key 使用稳定的小写英文路径，例如 communication.concise_reminders。
- 只有用户明确撤回或纠正已有记忆时才 archive，且必须使用 existing_memories 中已有的 memory_key。
- confidence_score 低于 70 的候选不要输出。
- 最多输出 8 个操作。没有可靠操作时返回 {"operations":[]}。
- 只返回符合 schema 的 JSON，不要 Markdown 或解释。
""".strip()

MEMORY_EXTRACTOR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "nightly_memory_operations",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operations"],
        "properties": {
            "operations": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "operation",
                        "memory_key",
                        "memory_type",
                        "summary",
                        "confidence_score",
                        "evidence_index",
                        "expires_in_days",
                    ],
                    "properties": {
                        "operation": {"type": "string", "enum": ["upsert", "archive"]},
                        "memory_key": {"type": "string", "minLength": 3, "maxLength": 120},
                        "memory_type": {"type": "string", "enum": list(MEMORY_TYPES)},
                        "summary": {"type": "string", "maxLength": 500},
                        "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "evidence_index": {"type": "integer", "minimum": 0},
                        "expires_in_days": {"type": ["integer", "null"], "minimum": 1, "maximum": 365},
                    },
                },
            }
        },
    },
}


@dataclass(frozen=True)
class MemorySourceMessage:
    index: int
    message_id: UUID
    run_id: UUID
    role: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class MemoryExistingItem:
    memory_id: UUID
    memory_key: str
    memory_type: str
    summary: str


@dataclass(frozen=True)
class MemoryConsolidationBatch:
    run_id: UUID
    owner_user_id: UUID
    source_date: date
    source_hash: str
    extractor_version: str
    messages: tuple[MemorySourceMessage, ...]
    existing_memories: tuple[MemoryExistingItem, ...]


@dataclass(frozen=True)
class MemoryConsolidationPreparation:
    status: str
    batch: MemoryConsolidationBatch | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    operation: str
    memory_key: str
    memory_type: str
    summary: str
    confidence_score: int
    source_message_id: UUID
    source_run_id: UUID
    evidence_index: int
    expires_in_days: int | None


@dataclass(frozen=True)
class MemoryConsolidationApplyResult:
    status: str
    run_id: UUID
    upserted_count: int
    archived_count: int
    rejected_count: int


class AgentMemoryExtractor:
    def __init__(self, *, model_runner: AgentModelRunner) -> None:
        self.model_runner = model_runner

    async def extract(self, *, batch: MemoryConsolidationBatch) -> list[MemoryCandidate]:
        result = await self.model_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(batch.run_id),
                thread_id=f"memory:{batch.owner_user_id}:{batch.source_date.isoformat()}",
                actor_user_id=str(batch.owner_user_id),
                instructions=MEMORY_EXTRACTOR_INSTRUCTIONS,
                model_input=[
                    {
                        "role": "user",
                        "content": json.dumps(_extractor_input(batch), ensure_ascii=False, sort_keys=True),
                    }
                ],
                prompt_version=batch.extractor_version,
                trace_id=f"memory-consolidation:{batch.run_id}",
                service_skill_id="memory-consolidator",
                response_text_format=MEMORY_EXTRACTOR_RESPONSE_FORMAT,
            )
        )
        raw_operations = _json_object_from_text(result.final_text).get("operations")
        return _normalize_candidates(raw_operations, batch=batch)


async def prepare_memory_consolidation(
    *,
    repository: AgentMemoryRepository,
    owner_user_id: UUID,
    source_date: date,
    range_start: datetime,
    range_end: datetime,
    extractor_version: str = MEMORY_EXTRACTOR_VERSION,
    now: datetime | None = None,
    message_limit: int = MAX_CONSOLIDATION_MESSAGES,
) -> MemoryConsolidationPreparation:
    settings = await repository.get_memory_settings(owner_user_id=owner_user_id)
    if settings is not None and not settings.memory_enabled:
        return MemoryConsolidationPreparation(status="disabled")

    raw_messages = await repository.list_completed_conversation_messages(
        owner_user_id=owner_user_id,
        range_start=range_start,
        range_end=range_end,
        limit=message_limit,
    )
    messages = _source_messages(raw_messages)
    if not messages:
        return MemoryConsolidationPreparation(status="empty")

    active_memories = await repository.list_active_memories(
        owner_user_id=owner_user_id,
        memory_type=None,
        limit=MAX_EXISTING_MEMORIES,
    )
    existing_memories = _existing_memory_items(active_memories)
    source_hash = _source_hash(messages=messages)
    normalized_version = extractor_version.strip() or MEMORY_EXTRACTOR_VERSION
    started_at = _aware_utc(now or datetime.now(timezone.utc))
    consolidation_run = await repository.get_consolidation_run(
        owner_user_id=owner_user_id,
        source_date=source_date,
        source_hash=source_hash,
        extractor_version=normalized_version,
    )
    if consolidation_run is not None and consolidation_run.status == "completed":
        return MemoryConsolidationPreparation(status="already_completed")
    if consolidation_run is not None and consolidation_run.status == "extracting":
        prior_started_at = _aware_utc(consolidation_run.started_at)
        if prior_started_at > started_at - STALE_CONSOLIDATION_AFTER:
            return MemoryConsolidationPreparation(status="in_progress")
    if consolidation_run is None:
        consolidation_run = await repository.create_consolidation_run(
            owner_user_id=owner_user_id,
            source_date=source_date,
            source_hash=source_hash,
            extractor_version=normalized_version,
            input_message_count=len(messages),
            started_at=started_at,
        )
    else:
        consolidation_run = await repository.restart_consolidation_run(
            run=consolidation_run,
            started_at=started_at,
            input_message_count=len(messages),
        )
    return MemoryConsolidationPreparation(
        status="ready",
        batch=MemoryConsolidationBatch(
            run_id=consolidation_run.id,
            owner_user_id=owner_user_id,
            source_date=source_date,
            source_hash=source_hash,
            extractor_version=normalized_version,
            messages=messages,
            existing_memories=existing_memories,
        ),
    )


async def apply_memory_consolidation(
    *,
    repository: AgentMemoryRepository,
    memory_service: AgentMemoryService,
    batch: MemoryConsolidationBatch,
    candidates: list[MemoryCandidate],
    now: datetime | None = None,
) -> MemoryConsolidationApplyResult:
    applied_at = _aware_utc(now or datetime.now(timezone.utc))
    settings = await repository.get_memory_settings(owner_user_id=batch.owner_user_id)
    if settings is not None and not settings.memory_enabled:
        await repository.complete_consolidation_run(
            run_id=batch.run_id,
            completed_at=applied_at,
            upserted_count=0,
            archived_count=0,
            rejected_count=len(candidates),
        )
        return MemoryConsolidationApplyResult(
            status="disabled",
            run_id=batch.run_id,
            upserted_count=0,
            archived_count=0,
            rejected_count=len(candidates),
        )
    upserted_count = 0
    archived_count = 0
    rejected_count = 0
    for candidate in candidates:
        if candidate.operation == "archive":
            archived = await repository.archive_memory_by_key(
                owner_user_id=batch.owner_user_id,
                memory_key=candidate.memory_key,
                archived_at=applied_at,
            )
            if archived is None:
                rejected_count += 1
            else:
                archived_count += 1
            continue
        try:
            content = validate_memory_write_policy({"summary": candidate.summary, "sensitivity": "normal"})
        except ApiError:
            rejected_count += 1
            continue
        expires_at = (
            applied_at + timedelta(days=candidate.expires_in_days)
            if candidate.expires_in_days is not None
            else None
        )
        await repository.upsert_memory_by_key(
            owner_user_id=batch.owner_user_id,
            memory_key=candidate.memory_key,
            memory_type=candidate.memory_type,
            content=content,
            schema_version="v1",
            source_run_id=candidate.source_run_id,
            source_message_id=candidate.source_message_id,
            confidence_score=candidate.confidence_score,
            expires_at=expires_at,
        )
        upserted_count += 1

    await memory_service.refresh_runtime_snapshot(
        owner_user_id=batch.owner_user_id,
        source_date=batch.source_date,
        extractor_version=batch.extractor_version,
    )
    await repository.complete_consolidation_run(
        run_id=batch.run_id,
        completed_at=applied_at,
        upserted_count=upserted_count,
        archived_count=archived_count,
        rejected_count=rejected_count,
    )
    return MemoryConsolidationApplyResult(
        status="completed",
        run_id=batch.run_id,
        upserted_count=upserted_count,
        archived_count=archived_count,
        rejected_count=rejected_count,
    )


async def fail_memory_consolidation(
    *,
    repository: AgentMemoryRepository,
    run_id: UUID,
    error_code: str,
    now: datetime | None = None,
) -> None:
    await repository.fail_consolidation_run(
        run_id=run_id,
        completed_at=_aware_utc(now or datetime.now(timezone.utc)),
        error_code=error_code,
    )


def _source_messages(raw_messages: list[Any]) -> tuple[MemorySourceMessage, ...]:
    messages: list[MemorySourceMessage] = []
    for message in raw_messages:
        content = message.content if isinstance(message.content, dict) else {}
        text = str(content.get("text") or "").strip()
        if not text or message.role not in {"user", "assistant"} or message.run_id is None:
            continue
        messages.append(
            MemorySourceMessage(
                index=len(messages),
                message_id=message.id,
                run_id=message.run_id,
                role=message.role,
                text=text[:2000],
                created_at=_aware_utc(message.created_at),
            )
        )
    return tuple(messages)


def _existing_memory_items(raw_memories: list[Any]) -> tuple[MemoryExistingItem, ...]:
    items: list[MemoryExistingItem] = []
    for memory in raw_memories:
        content = memory.content if isinstance(memory.content, dict) else {}
        memory_key = str(memory.memory_key or "").strip()
        summary = str(content.get("summary") or "").strip()
        if not memory_key or not summary:
            continue
        items.append(
            MemoryExistingItem(
                memory_id=memory.id,
                memory_key=memory_key,
                memory_type=memory.memory_type,
                summary=summary,
            )
        )
    return tuple(items)


def _extractor_input(batch: MemoryConsolidationBatch) -> dict[str, Any]:
    return {
        "source_date": batch.source_date.isoformat(),
        "existing_memories": [
            {
                "memory_key": item.memory_key,
                "memory_type": item.memory_type,
                "summary": item.summary,
            }
            for item in batch.existing_memories
        ],
        "dialogue": [
            {
                "index": message.index,
                "role": message.role,
                "text": message.text,
            }
            for message in batch.messages
        ],
    }


def _normalize_candidates(value: Any, *, batch: MemoryConsolidationBatch) -> list[MemoryCandidate]:
    if not isinstance(value, list):
        return []
    message_by_index = {message.index: message for message in batch.messages}
    existing_keys = {item.memory_key for item in batch.existing_memories}
    candidates_by_key: dict[str, MemoryCandidate] = {}
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        operation = str(item.get("operation") or "").strip()
        memory_key = str(item.get("memory_key") or "").strip().lower()
        memory_type = str(item.get("memory_type") or "").strip()
        confidence_score = item.get("confidence_score")
        evidence_index = item.get("evidence_index")
        expires_in_days = item.get("expires_in_days")
        if operation not in {"upsert", "archive"} or MEMORY_KEY_PATTERN.fullmatch(memory_key) is None:
            continue
        if memory_type not in MEMORY_TYPES:
            continue
        if isinstance(confidence_score, bool) or not isinstance(confidence_score, int):
            continue
        if confidence_score < MIN_MEMORY_CONFIDENCE or confidence_score > 100:
            continue
        if isinstance(evidence_index, bool) or not isinstance(evidence_index, int):
            continue
        source_message = message_by_index.get(evidence_index)
        if source_message is None or source_message.role != "user":
            continue
        if expires_in_days is not None and (
            isinstance(expires_in_days, bool)
            or not isinstance(expires_in_days, int)
            or expires_in_days < 1
            or expires_in_days > 365
        ):
            continue
        summary = str(item.get("summary") or "").strip()
        if operation == "archive":
            if memory_key not in existing_keys:
                continue
            summary = ""
        else:
            try:
                summary = validate_memory_write_policy({"summary": summary, "sensitivity": "normal"})["summary"]
            except ApiError:
                continue
        candidate = MemoryCandidate(
            operation=operation,
            memory_key=memory_key,
            memory_type=memory_type,
            summary=summary,
            confidence_score=confidence_score,
            source_message_id=source_message.message_id,
            source_run_id=source_message.run_id,
            evidence_index=evidence_index,
            expires_in_days=expires_in_days,
        )
        prior = candidates_by_key.get(memory_key)
        if prior is None or candidate.evidence_index >= prior.evidence_index:
            candidates_by_key[memory_key] = candidate
    return list(candidates_by_key.values())


def _source_hash(
    *,
    messages: tuple[MemorySourceMessage, ...],
) -> str:
    payload = {
        "messages": [
            {
                "id": str(item.message_id),
                "run_id": str(item.run_id),
                "role": item.role,
                "text": item.text,
            }
            for item in messages
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
