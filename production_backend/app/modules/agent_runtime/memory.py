from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from .models import MEMORY_TYPES, AgentMemory


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
        normalized_type = _normalize_memory_type(memory_type)
        normalized_content = _normalize_content(content)
        return await self.repository.create_memory(
            owner_user_id=owner_user_id,
            memory_type=normalized_type,
            content=normalized_content,
            schema_version=schema_version.strip() or "v1",
            source_run_id=source_run_id,
            source_message_id=source_message_id,
            confidence_score=_bounded_confidence(confidence_score),
            expires_at=expires_at,
        )

    async def list_active_memories(
        self,
        *,
        owner_user_id: UUID,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[AgentMemory]:
        if memory_type is not None:
            memory_type = _normalize_memory_type(memory_type)
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_active_memories(owner_user_id=owner_user_id, memory_type=memory_type, limit=limit)

    async def archive_memory(self, *, owner_user_id: UUID, memory_id: UUID) -> AgentMemory:
        memory = await self.repository.archive_memory(
            owner_user_id=owner_user_id,
            memory_id=memory_id,
            archived_at=datetime.now(timezone.utc),
        )
        if memory is None:
            raise ApiError(code="not_found", message="Agent memory not found.", status=404)
        return memory


def _normalize_memory_type(memory_type: str) -> str:
    normalized = str(memory_type or "").strip()
    if normalized not in MEMORY_TYPES:
        raise ApiError(code="unsupported_memory_type", message="Agent memory type is not supported.", status=422)
    return normalized


def _normalize_content(content: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, dict) or not content:
        raise ApiError(code="validation_failed", message="memory content must be a non-empty object.", status=422)
    return dict(content)


def _bounded_confidence(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
        raise ApiError(code="validation_failed", message="confidence_score must be between 0 and 100.", status=422)
    return value
