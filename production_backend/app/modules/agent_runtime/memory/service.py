from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.errors import ApiError
from ..models import (
    MEMORY_TYPES,
    AgentMemory,
    AgentMemoryConsolidationRun,
    AgentMemorySettings,
    AgentMemorySnapshot,
    AgentMessage,
    AgentRun,
    AgentThread,
)


ALLOWED_MEMORY_SENSITIVITIES = frozenset({"normal", "personal"})
BLOCKED_MEMORY_SENSITIVITIES = frozenset({"health", "child", "crisis", "regulated", "financial", "legal"})
MAX_MEMORY_RETENTION_DAYS = 365
MAX_RUNTIME_MEMORY_ITEMS = 5
SENSITIVE_MEMORY_SUMMARY_TERMS = (
    "diagnosis",
    "diagnosed",
    "medication",
    "fever",
    "bleeding",
    "severe pain",
    "newborn",
    "infant",
    "baby has",
    "suicide",
    "hurt myself",
    "hurt my baby",
    "medical",
    "胎动",
    "发烧",
    "出血",
    "自杀",
    "伤害自己",
    "伤害宝宝",
)


class AgentMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_memory(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str,
        content: dict[str, Any],
        schema_version: str,
        source_run_id: UUID | None,
        source_message_id: UUID | None,
        confidence_score: int,
        expires_at: datetime | None,
    ) -> AgentMemory:
        memory = AgentMemory(
            owner_user_id=owner_user_id,
            memory_type=memory_type,
            content=content,
            schema_version=schema_version,
            source_run_id=source_run_id,
            source_message_id=source_message_id,
            confidence_score=confidence_score,
            expires_at=expires_at,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_memory_settings(self, *, owner_user_id: UUID) -> AgentMemorySettings | None:
        return await self.session.get(AgentMemorySettings, owner_user_id)

    async def upsert_memory_settings(
        self,
        *,
        owner_user_id: UUID,
        memory_enabled: bool,
    ) -> AgentMemorySettings:
        settings = await self.get_memory_settings(owner_user_id=owner_user_id)
        if settings is None:
            settings = AgentMemorySettings(owner_user_id=owner_user_id)
            self.session.add(settings)
        settings.memory_enabled = memory_enabled
        settings.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return settings

    async def get_memory_snapshot(self, *, owner_user_id: UUID) -> AgentMemorySnapshot | None:
        return await self.session.get(AgentMemorySnapshot, owner_user_id)

    async def upsert_memory_snapshot(
        self,
        *,
        owner_user_id: UUID,
        items: list[dict[str, Any]],
        source_date: date | None = None,
        extractor_version: str = "",
    ) -> AgentMemorySnapshot:
        snapshot = await self.get_memory_snapshot(owner_user_id=owner_user_id)
        if snapshot is None:
            snapshot = AgentMemorySnapshot(owner_user_id=owner_user_id)
            self.session.add(snapshot)
        snapshot.items = items
        snapshot.source_date = source_date
        snapshot.extractor_version = extractor_version.strip()
        snapshot.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return snapshot

    async def list_conversation_owner_ids(
        self,
        *,
        range_start: datetime,
        range_end: datetime,
        limit: int,
    ) -> list[UUID]:
        statement = (
            select(AgentThread.owner_user_id)
            .join(AgentMessage, AgentMessage.thread_id == AgentThread.id)
            .join(AgentRun, AgentRun.id == AgentMessage.run_id)
            .where(
                AgentMessage.created_at >= range_start,
                AgentMessage.created_at < range_end,
                AgentMessage.status == "completed",
                AgentMessage.role.in_(("user", "assistant")),
                AgentRun.status == "completed",
                AgentThread.deleted_at.is_(None),
            )
            .distinct()
            .order_by(AgentThread.owner_user_id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def list_completed_conversation_messages(
        self,
        *,
        owner_user_id: UUID,
        range_start: datetime,
        range_end: datetime,
        limit: int,
    ) -> list[AgentMessage]:
        statement = (
            select(AgentMessage)
            .join(AgentThread, AgentThread.id == AgentMessage.thread_id)
            .join(AgentRun, AgentRun.id == AgentMessage.run_id)
            .where(
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
                AgentMessage.created_at >= range_start,
                AgentMessage.created_at < range_end,
                AgentMessage.status == "completed",
                AgentMessage.role.in_(("user", "assistant")),
                AgentRun.status == "completed",
            )
            .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def get_consolidation_run(
        self,
        *,
        owner_user_id: UUID,
        source_date: date,
        source_hash: str,
        extractor_version: str,
    ) -> AgentMemoryConsolidationRun | None:
        statement = select(AgentMemoryConsolidationRun).where(
            AgentMemoryConsolidationRun.owner_user_id == owner_user_id,
            AgentMemoryConsolidationRun.source_date == source_date,
            AgentMemoryConsolidationRun.source_hash == source_hash,
            AgentMemoryConsolidationRun.extractor_version == extractor_version,
        )
        return await self.session.scalar(statement)

    async def create_consolidation_run(
        self,
        *,
        owner_user_id: UUID,
        source_date: date,
        source_hash: str,
        extractor_version: str,
        input_message_count: int,
        started_at: datetime,
    ) -> AgentMemoryConsolidationRun:
        run = AgentMemoryConsolidationRun(
            owner_user_id=owner_user_id,
            source_date=source_date,
            source_hash=source_hash,
            extractor_version=extractor_version,
            status="extracting",
            input_message_count=input_message_count,
            started_at=started_at,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def restart_consolidation_run(
        self,
        *,
        run: AgentMemoryConsolidationRun,
        started_at: datetime,
        input_message_count: int,
    ) -> AgentMemoryConsolidationRun:
        run.status = "extracting"
        run.input_message_count = input_message_count
        run.upserted_count = 0
        run.archived_count = 0
        run.rejected_count = 0
        run.error_code = ""
        run.started_at = started_at
        run.completed_at = None
        await self.session.flush()
        return run

    async def complete_consolidation_run(
        self,
        *,
        run_id: UUID,
        completed_at: datetime,
        upserted_count: int,
        archived_count: int,
        rejected_count: int,
    ) -> AgentMemoryConsolidationRun:
        run = await self.session.get(AgentMemoryConsolidationRun, run_id)
        if run is None:
            raise ApiError(code="not_found", message="Memory consolidation run not found.", status=404)
        run.status = "completed"
        run.upserted_count = upserted_count
        run.archived_count = archived_count
        run.rejected_count = rejected_count
        run.error_code = ""
        run.completed_at = completed_at
        await self.session.flush()
        return run

    async def fail_consolidation_run(
        self,
        *,
        run_id: UUID,
        completed_at: datetime,
        error_code: str,
    ) -> AgentMemoryConsolidationRun | None:
        run = await self.session.get(AgentMemoryConsolidationRun, run_id)
        if run is None:
            return None
        run.status = "failed"
        run.error_code = error_code[:120]
        run.completed_at = completed_at
        await self.session.flush()
        return run

    async def upsert_memory_by_key(
        self,
        *,
        owner_user_id: UUID,
        memory_key: str,
        memory_type: str,
        content: dict[str, Any],
        schema_version: str,
        source_run_id: UUID,
        source_message_id: UUID,
        confidence_score: int,
        expires_at: datetime | None,
    ) -> tuple[AgentMemory, bool]:
        statement = select(AgentMemory).where(
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.memory_key == memory_key,
        )
        memory = await self.session.scalar(statement)
        created = memory is None
        if memory is None:
            memory = AgentMemory(owner_user_id=owner_user_id, memory_key=memory_key)
            self.session.add(memory)
        memory.memory_type = memory_type
        memory.content = content
        memory.schema_version = schema_version
        memory.source_run_id = source_run_id
        memory.source_message_id = source_message_id
        memory.confidence_score = confidence_score
        memory.expires_at = expires_at
        memory.status = "active"
        memory.archived_at = None
        await self.session.flush()
        return memory, created

    async def archive_memory_by_key(
        self,
        *,
        owner_user_id: UUID,
        memory_key: str,
        archived_at: datetime,
    ) -> AgentMemory | None:
        statement = select(AgentMemory).where(
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.memory_key == memory_key,
            AgentMemory.status == "active",
        )
        memory = await self.session.scalar(statement)
        if memory is None:
            return None
        memory.status = "archived"
        memory.archived_at = archived_at
        await self.session.flush()
        return memory

    async def list_active_memories(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str | None,
        limit: int,
    ) -> list[AgentMemory]:
        conditions = [
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.status == "active",
            or_(AgentMemory.expires_at.is_(None), AgentMemory.expires_at > datetime.now(timezone.utc)),
        ]
        if memory_type:
            conditions.append(AgentMemory.memory_type == memory_type)
        statement = select(AgentMemory).where(*conditions).order_by(AgentMemory.updated_at.desc(), AgentMemory.id.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def archive_memory(self, *, owner_user_id: UUID, memory_id: UUID, archived_at: datetime) -> AgentMemory | None:
        statement = select(AgentMemory).where(
            AgentMemory.id == memory_id,
            AgentMemory.owner_user_id == owner_user_id,
            AgentMemory.status == "active",
        )
        memory = await self.session.scalar(statement)
        if memory is None:
            return None
        memory.status = "archived"
        memory.archived_at = archived_at
        await self.session.flush()
        return memory


class AgentMemoryService:
    def __init__(self, *, repository: AgentMemoryRepository) -> None:
        self.repository = repository

    async def create_memory(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str,
        content: dict[str, Any],
        schema_version: str = "v1",
        source_run_id: UUID | None = None,
        source_message_id: UUID | None = None,
        confidence_score: int = 0,
        expires_at: datetime | None = None,
    ) -> AgentMemory:
        if not await self.is_memory_enabled(owner_user_id=owner_user_id):
            raise ApiError(code="memory_disabled", message="Agent memory is disabled for this user.", status=403)
        normalized_type = _normalize_memory_type(memory_type)
        normalized_content = _normalize_content(content)
        normalized_expires_at = _normalize_expires_at(expires_at)
        return await self.repository.create_memory(
            owner_user_id=owner_user_id,
            memory_type=normalized_type,
            content=normalized_content,
            schema_version=schema_version.strip() or "v1",
            source_run_id=source_run_id,
            source_message_id=source_message_id,
            confidence_score=_bounded_confidence(confidence_score),
            expires_at=normalized_expires_at,
        )

    async def list_active_memories(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str | None = None,
        limit: int = 20,
        include_when_disabled: bool = False,
    ) -> list[AgentMemory]:
        if not include_when_disabled and not await self.is_memory_enabled(owner_user_id=owner_user_id):
            return []
        if memory_type is not None:
            memory_type = _normalize_memory_type(memory_type)
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_active_memories(owner_user_id=owner_user_id, memory_type=memory_type, limit=limit)

    async def get_runtime_snapshot(self, *, owner_user_id: UUID, limit: int = MAX_RUNTIME_MEMORY_ITEMS) -> list[dict[str, Any]]:
        if limit < 1 or limit > MAX_RUNTIME_MEMORY_ITEMS:
            raise ApiError(
                code="validation_failed",
                message=f"runtime memory snapshot limit must be between 1 and {MAX_RUNTIME_MEMORY_ITEMS}.",
                status=422,
            )
        snapshot = await self.repository.get_memory_snapshot(owner_user_id=owner_user_id)
        if snapshot is None or not isinstance(snapshot.items, list):
            return []
        now = datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        for value in snapshot.items:
            item = _runtime_snapshot_item(value, now=now)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        return items

    async def refresh_runtime_snapshot(
        self,
        *,
        owner_user_id: UUID,
        source_date: date | None = None,
        extractor_version: str = "",
    ) -> AgentMemorySnapshot:
        memories = await self.repository.list_active_memories(
            owner_user_id=owner_user_id,
            memory_type=None,
            limit=MAX_RUNTIME_MEMORY_ITEMS,
        )
        return await self.repository.upsert_memory_snapshot(
            owner_user_id=owner_user_id,
            items=[_snapshot_item(memory) for memory in memories],
            source_date=source_date,
            extractor_version=extractor_version,
        )

    async def get_settings(self, *, owner_user_id: UUID) -> AgentMemorySettings:
        settings = await self.repository.get_memory_settings(owner_user_id=owner_user_id)
        if settings is not None:
            return settings
        return AgentMemorySettings(owner_user_id=owner_user_id, memory_enabled=True)

    async def update_settings(self, *, owner_user_id: UUID, memory_enabled: bool) -> AgentMemorySettings:
        if not isinstance(memory_enabled, bool):
            raise ApiError(code="validation_failed", message="memory_enabled must be a boolean.", status=422)
        settings = await self.repository.upsert_memory_settings(owner_user_id=owner_user_id, memory_enabled=memory_enabled)
        if memory_enabled:
            await self.refresh_runtime_snapshot(owner_user_id=owner_user_id)
        else:
            await self.repository.upsert_memory_snapshot(owner_user_id=owner_user_id, items=[])
        return settings

    async def is_memory_enabled(self, *, owner_user_id: UUID) -> bool:
        return (await self.get_settings(owner_user_id=owner_user_id)).memory_enabled

    async def archive_memory(self, *, owner_user_id: UUID, memory_id: UUID) -> AgentMemory:
        memory = await self.repository.archive_memory(
            owner_user_id=owner_user_id,
            memory_id=memory_id,
            archived_at=datetime.now(timezone.utc),
        )
        if memory is None:
            raise ApiError(code="not_found", message="Agent memory not found.", status=404)
        await self.refresh_runtime_snapshot(owner_user_id=owner_user_id)
        return memory


def _normalize_memory_type(memory_type: str) -> str:
    normalized = str(memory_type or "").strip()
    if normalized not in MEMORY_TYPES:
        raise ApiError(code="unsupported_memory_type", message="Agent memory type is not supported.", status=422)
    return normalized


def _normalize_content(content: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, dict) or not content:
        raise ApiError(code="validation_failed", message="memory content must be a non-empty object.", status=422)
    normalized = dict(content)
    summary = str(normalized.get("summary") or "").strip()
    if not summary:
        raise ApiError(code="validation_failed", message="memory content.summary is required.", status=422)
    if len(summary) > 500:
        raise ApiError(code="validation_failed", message="memory content.summary must be 500 characters or fewer.", status=422)
    sensitivity = str(normalized.get("sensitivity") or "normal").strip().lower()
    if sensitivity in BLOCKED_MEMORY_SENSITIVITIES:
        raise ApiError(code="sensitive_memory_not_allowed", message="Sensitive memory writes are not enabled.", status=422)
    if sensitivity not in ALLOWED_MEMORY_SENSITIVITIES:
        raise ApiError(code="validation_failed", message="memory content.sensitivity is not supported.", status=422)
    if _looks_sensitive_summary(summary):
        raise ApiError(code="sensitive_memory_not_allowed", message="Sensitive memory writes are not enabled.", status=422)
    normalized["summary"] = summary
    normalized["sensitivity"] = sensitivity
    return normalized


def validate_memory_write_policy(content: dict[str, Any]) -> dict[str, Any]:
    return _normalize_content(content)


def _bounded_confidence(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
        raise ApiError(code="validation_failed", message="confidence_score must be between 0 and 100.", status=422)
    return value


def _normalize_expires_at(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    now = datetime.now(timezone.utc)
    normalized = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    if normalized <= now:
        raise ApiError(code="validation_failed", message="memory expires_at must be in the future.", status=422)
    if normalized > now + timedelta(days=MAX_MEMORY_RETENTION_DAYS):
        raise ApiError(code="validation_failed", message="memory expires_at must be within retention policy.", status=422)
    return normalized


def _looks_sensitive_summary(summary: str) -> bool:
    normalized = " ".join(summary.lower().split())
    return any(term.lower() in normalized for term in SENSITIVE_MEMORY_SUMMARY_TERMS)


def _snapshot_item(memory: AgentMemory) -> dict[str, Any]:
    content = memory.content if isinstance(memory.content, dict) else {}
    return {
        "memory_id": str(memory.id),
        "memory_key": str(memory.memory_key or ""),
        "memory_type": memory.memory_type,
        "summary": str(content.get("summary") or "").strip(),
        "confidence_score": int(memory.confidence_score or 0),
        "expires_at": memory.expires_at.isoformat() if memory.expires_at is not None else "",
    }


def _runtime_snapshot_item(value: Any, *, now: datetime) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    memory_type = str(value.get("memory_type") or "").strip()
    summary = str(value.get("summary") or "").strip()
    if memory_type not in MEMORY_TYPES or not summary:
        return None
    raw_expires_at = str(value.get("expires_at") or "").strip()
    if raw_expires_at:
        try:
            expires_at = datetime.fromisoformat(raw_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        comparable_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
        if comparable_expires_at <= now:
            return None
    return {"memory_type": memory_type, "summary": summary[:500]}
