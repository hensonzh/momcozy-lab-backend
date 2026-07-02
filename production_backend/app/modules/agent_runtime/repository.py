from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentAction, AgentEvent, AgentMessage, AgentRun, AgentThread


class AgentRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str,
        metadata: dict[str, Any],
    ) -> AgentThread:
        thread = AgentThread(owner_user_id=owner_user_id, title=title, metadata_json=metadata)
        self.session.add(thread)
        await self.session.flush()
        return thread

    async def get_thread_for_owner(self, *, thread_id: UUID, owner_user_id: UUID) -> AgentThread | None:
        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.owner_user_id == owner_user_id,
            AgentThread.deleted_at.is_(None),
        )
        return await self.session.scalar(statement)

    async def list_threads_for_owner(self, *, owner_user_id: UUID, limit: int) -> list[AgentThread]:
        statement = (
            select(AgentThread)
            .where(AgentThread.owner_user_id == owner_user_id, AgentThread.deleted_at.is_(None))
            .order_by(AgentThread.updated_at.desc(), AgentThread.id.desc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_run(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
        runtime_pattern: str,
        graph_version: str,
        prompt_version: str,
        request_id: str,
        trace_id: str,
    ) -> AgentRun:
        run = AgentRun(
            thread_id=thread_id,
            actor_user_id=actor_user_id,
            runtime_pattern=runtime_pattern,
            graph_version=graph_version,
            prompt_version=prompt_version,
            request_id=request_id,
            trace_id=trace_id,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run_for_owner(self, *, run_id: UUID, owner_user_id: UUID) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(AgentRun.id == run_id, AgentThread.owner_user_id == owner_user_id, AgentThread.deleted_at.is_(None))
        )
        return await self.session.scalar(statement)

    async def create_message(
        self,
        *,
        thread_id: UUID,
        run_id: UUID | None,
        role: str,
        message_type: str,
        content: dict[str, Any],
        status: str,
    ) -> AgentMessage:
        sequence = await self._next_thread_message_sequence(thread_id=thread_id)
        message = AgentMessage(
            thread_id=thread_id,
            run_id=run_id,
            role=role,
            message_type=message_type,
            content=content,
            status=status,
            sequence=sequence,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def append_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        sequence = await self._next_run_event_sequence(run_id=run_id)
        event = AgentEvent(thread_id=thread_id, run_id=run_id, sequence=sequence, event_type=event_type, payload=payload)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events_for_owner(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> list[AgentEvent]:
        statement = (
            select(AgentEvent)
            .join(AgentRun, AgentRun.id == AgentEvent.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentEvent.run_id == run_id,
                AgentEvent.sequence > after_sequence,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentEvent.sequence)
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_action(
        self,
        *,
        run_id: UUID,
        actor_user_id: UUID,
        action_type: str,
        target_type: str,
        target_id: str,
        status: str,
        side_effect_level: str,
        preview_payload: dict[str, Any],
        apply_payload: dict[str, Any],
        idempotency_key: str,
        expires_at: datetime | None,
    ) -> AgentAction:
        action = AgentAction(
            run_id=run_id,
            actor_user_id=actor_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            status=status,
            side_effect_level=side_effect_level,
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID) -> AgentAction | None:
        statement = (
            select(AgentAction)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentAction.id == action_id,
                AgentAction.actor_user_id == owner_user_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return await self.session.scalar(statement)

    async def mark_action_confirmed(
        self,
        *,
        action: AgentAction,
        confirmed_at: datetime,
        apply_payload: dict[str, Any] | None,
        idempotency_key: str,
    ) -> AgentAction:
        action.status = "confirmed"
        action.confirmed_at = confirmed_at
        if apply_payload is not None:
            action.apply_payload = apply_payload
        if idempotency_key:
            action.idempotency_key = idempotency_key
        await self.session.flush()
        return action

    async def mark_action_rejected(self, *, action: AgentAction, failed_at: datetime, error_code: str) -> AgentAction:
        action.status = "rejected"
        action.failed_at = failed_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def mark_run_cancelled(
        self,
        *,
        run: AgentRun,
        cancelled_at: datetime,
        error_code: str,
    ) -> AgentRun:
        run.status = "cancelled"
        run.cancelled_at = cancelled_at
        run.completed_at = cancelled_at
        run.error_code = error_code
        await self.session.flush()
        return run

    async def _next_thread_message_sequence(self, *, thread_id: UUID) -> int:
        statement = select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(AgentMessage.thread_id == thread_id)
        return int(await self.session.scalar(statement) or 1)

    async def _next_run_event_sequence(self, *, run_id: UUID) -> int:
        statement = select(func.coalesce(func.max(AgentEvent.sequence), 0) + 1).where(AgentEvent.run_id == run_id)
        return int(await self.session.scalar(statement) or 1)
