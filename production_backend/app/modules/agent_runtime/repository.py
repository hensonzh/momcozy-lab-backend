from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db.session import add_after_commit_callback
from .models import (
    ACTIVE_RUN_STATUSES,
    AgentAction,
    AgentArtifact,
    AgentContextCheckpoint,
    AgentContextProjection,
    AgentEvent,
    AgentEvalCase,
    AgentMessage,
    AgentRunSummary,
    AgentRoutingDecision,
    AgentRun,
    AgentSafetyEvent,
    AgentThread,
    AgentToolCall,
    AgentToolOutput,
    AgentWorkflowState,
)


class AgentRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def begin_nested(self):
        return self.session.begin_nested()

    async def rollback(self) -> None:
        await self.session.rollback()

    def add_after_commit_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        add_after_commit_callback(self.session, callback)

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
        return cast(AgentThread | None, await self.session.scalar(statement))

    async def list_threads_for_owner(self, *, owner_user_id: UUID, limit: int) -> list[AgentThread]:
        statement = (
            select(AgentThread)
            .where(AgentThread.owner_user_id == owner_user_id, AgentThread.deleted_at.is_(None))
            .order_by(AgentThread.updated_at.desc(), AgentThread.id.desc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def touch_thread(self, *, thread: AgentThread, updated_at: datetime) -> AgentThread:
        thread.updated_at = updated_at
        await self.session.flush()
        return thread

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

    async def record_routing_decision(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        message_id: UUID,
        selected_skill_id: str,
        routing_source: str,
        confidence: float,
        execution_mode: str,
        intents: list[Any],
        reason_codes: list[str],
        safety_flags: list[str],
        needs_clarification: bool,
        tool_scope_version: str = "default",
    ) -> AgentRoutingDecision:
        confidence_score = max(0, min(100, round(confidence * 100)))
        run = await self.get_run(run_id=run_id)
        if run is not None:
            run.service_skill_id = selected_skill_id
            run.routing_source = routing_source
            run.routing_confidence_score = confidence_score
            run.routing_summary = {
                "execution_mode": execution_mode,
                "reason_codes": reason_codes,
                "safety_flags": safety_flags,
                "needs_clarification": needs_clarification,
                "tool_scope_version": tool_scope_version,
            }

        statement = select(AgentRoutingDecision).where(
            AgentRoutingDecision.run_id == run_id,
            AgentRoutingDecision.message_id == message_id,
        )
        decision = cast(AgentRoutingDecision | None, await self.session.scalar(statement))
        if decision is None:
            decision = AgentRoutingDecision(
                run_id=run_id,
                thread_id=thread_id,
                actor_user_id=actor_user_id,
                message_id=message_id,
                selected_skill_id=selected_skill_id,
                routing_source=routing_source,
                confidence_score=confidence_score,
                execution_mode=execution_mode,
                intents=intents,
                reason_codes=reason_codes,
                safety_flags=safety_flags,
                needs_clarification=needs_clarification,
                tool_scope_version=tool_scope_version,
            )
            self.session.add(decision)
        else:
            decision.selected_skill_id = selected_skill_id
            decision.routing_source = routing_source
            decision.confidence_score = confidence_score
            decision.execution_mode = execution_mode
            decision.intents = intents
            decision.reason_codes = reason_codes
            decision.safety_flags = safety_flags
            decision.needs_clarification = needs_clarification
            decision.tool_scope_version = tool_scope_version
        await self.session.flush()
        return decision

    async def get_run_for_owner(self, *, run_id: UUID, owner_user_id: UUID) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(AgentRun.id == run_id, AgentThread.owner_user_id == owner_user_id, AgentThread.deleted_at.is_(None))
        )
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def get_run(self, *, run_id: UUID) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id).execution_options(populate_existing=True)
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def get_tool_call(self, *, tool_call_id: UUID) -> AgentToolCall | None:
        statement = select(AgentToolCall).where(AgentToolCall.id == tool_call_id).execution_options(populate_existing=True)
        return cast(AgentToolCall | None, await self.session.scalar(statement))

    async def get_active_run_for_thread(self, *, thread_id: UUID, owner_user_id: UUID) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            .limit(1)
        )
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def refresh_run(self, *, run: AgentRun) -> AgentRun:
        await self.session.refresh(run)
        return run

    async def list_runnable_runs(
        self,
        *,
        limit: int,
    ) -> list[AgentRun]:
        statement = select(AgentRun).where(AgentRun.status == "queued").order_by(AgentRun.created_at.asc(), AgentRun.id.asc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_stale_running_runs(
        self,
        *,
        cutoff: datetime | None,
        limit: int,
    ) -> list[AgentRun]:
        if cutoff is None:
            return []
        statement = (
            select(AgentRun)
            .where(
                AgentRun.status == "running",
                or_(AgentRun.started_at.is_(None), AgentRun.started_at <= cutoff),
            )
            .order_by(AgentRun.started_at.asc().nullsfirst(), AgentRun.created_at.asc(), AgentRun.id.asc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def mark_run_running(self, *, run: AgentRun, started_at: datetime) -> AgentRun:
        run.status = "running"
        if run.started_at is None:
            run.started_at = started_at
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def mark_run_completed(self, *, run: AgentRun, completed_at: datetime) -> AgentRun:
        run.status = "completed"
        run.completed_at = completed_at
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def mark_run_waiting_for_confirmation(self, *, run: AgentRun) -> AgentRun:
        run.status = "waiting_for_confirmation"
        await self.session.flush()
        return run

    async def mark_run_queued(self, *, run: AgentRun) -> AgentRun:
        run.status = "queued"
        run.started_at = None
        run.completed_at = None
        run.error_code = ""
        run.error_details = {}
        await self.session.flush()
        return run

    async def create_message(
        self,
        *,
        message_id: UUID | None = None,
        thread_id: UUID,
        run_id: UUID | None,
        role: str,
        message_type: str,
        content: dict[str, Any],
        status: str,
    ) -> AgentMessage:
        sequence = await self._next_thread_message_sequence(thread_id=thread_id)
        identity = {"id": message_id} if message_id is not None else {}
        message = AgentMessage(
            **identity,
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

    async def list_messages_for_thread(self, *, thread_id: UUID, limit: int = 40) -> list[AgentMessage]:
        statement = (
            select(AgentMessage)
            .where(AgentMessage.thread_id == thread_id)
            .order_by(AgentMessage.sequence.desc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        messages = list(result.all())
        messages.reverse()
        return messages

    async def get_latest_user_message_for_run(self, *, run_id: UUID) -> AgentMessage | None:
        statement = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id, AgentMessage.role == "user")
            .order_by(AgentMessage.sequence.desc())
            .limit(1)
        )
        return cast(AgentMessage | None, await self.session.scalar(statement))

    async def get_latest_assistant_message_for_run(self, *, run_id: UUID) -> AgentMessage | None:
        statement = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id, AgentMessage.role == "assistant", AgentMessage.status == "completed")
            .order_by(AgentMessage.sequence.desc())
            .limit(1)
        )
        return cast(AgentMessage | None, await self.session.scalar(statement))

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

    async def list_events_for_run(self, *, run_id: UUID) -> list[AgentEvent]:
        statement = select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.sequence)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_client_events_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int = 10,
    ) -> list[AgentEvent]:
        bounded_limit = max(1, min(limit, 50))
        statement = (
            select(AgentEvent)
            .join(AgentThread, AgentThread.id == AgentEvent.thread_id)
            .where(
                AgentEvent.thread_id == thread_id,
                AgentEvent.event_type == "client.event",
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentEvent.created_at.desc(), AgentEvent.event_id.desc())
            .limit(bounded_limit)
        )
        result = await self.session.scalars(statement)
        return list(reversed(result.all()))

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

    async def lock_run_for_action_proposal(self, *, run_id: UUID) -> None:
        await self.session.scalar(select(AgentRun.id).where(AgentRun.id == run_id).with_for_update())

    async def get_reusable_action_by_idempotency_key(
        self,
        *,
        run_id: UUID,
        actor_user_id: UUID,
        action_type: str,
        idempotency_key: str,
    ) -> AgentAction | None:
        statement = (
            select(AgentAction)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentAction.actor_user_id == actor_user_id,
                AgentAction.run_id == run_id,
                AgentAction.action_type == action_type,
                AgentAction.idempotency_key == idempotency_key,
                AgentAction.status.in_(("confirmation_required", "proposed", "confirmed", "applying", "applied")),
                AgentThread.owner_user_id == actor_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .order_by(AgentAction.created_at.desc())
            .limit(1)
        )
        return cast(AgentAction | None, await self.session.scalar(statement))

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
        return cast(AgentAction | None, await self.session.scalar(statement))

    async def lock_action_for_confirmation(self, *, action_id: UUID, owner_user_id: UUID) -> None:
        statement = (
            select(AgentAction.id)
            .join(AgentRun, AgentRun.id == AgentAction.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentAction.id == action_id,
                AgentAction.actor_user_id == owner_user_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
            .with_for_update()
        )
        await self.session.scalar(statement)

    async def get_action(self, *, action_id: UUID) -> AgentAction | None:
        statement = select(AgentAction).where(AgentAction.id == action_id).execution_options(populate_existing=True)
        return cast(AgentAction | None, await self.session.scalar(statement))

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

    async def mark_action_applying(self, *, action: AgentAction) -> AgentAction:
        action.status = "applying"
        action.error_code = ""
        await self.session.flush()
        return action

    async def mark_action_applied(self, *, action: AgentAction, applied_at: datetime) -> AgentAction:
        action.status = "applied"
        action.applied_at = applied_at
        action.error_code = ""
        await self.session.flush()
        return action

    async def mark_action_failed(self, *, action: AgentAction, failed_at: datetime, error_code: str) -> AgentAction:
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def mark_action_expired(self, *, action: AgentAction, failed_at: datetime, error_code: str) -> AgentAction:
        action.status = "expired"
        action.failed_at = failed_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def mark_action_rejected(self, *, action: AgentAction, failed_at: datetime, error_code: str) -> AgentAction:
        action.status = "rejected"
        action.failed_at = failed_at
        action.error_code = error_code
        await self.session.flush()
        return action

    async def start_tool_call(
        self,
        *,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        safe_args: dict[str, Any],
        started_at: datetime,
    ) -> AgentToolCall:
        tool_call = AgentToolCall(
            run_id=run_id,
            tool_name=tool_name,
            call_id=call_id,
            status="started",
            safe_args=safe_args,
            started_at=started_at,
        )
        self.session.add(tool_call)
        await self.session.flush()
        return tool_call

    async def complete_tool_call(
        self,
        *,
        tool_call: AgentToolCall,
        completed_at: datetime,
    ) -> AgentToolCall:
        tool_call.status = "completed"
        tool_call.completed_at = completed_at
        tool_call.error_code = ""
        await self.session.flush()
        return tool_call

    async def list_tool_calls_for_run(self, *, run_id: UUID) -> list[AgentToolCall]:
        statement = select(AgentToolCall).where(AgentToolCall.run_id == run_id).order_by(AgentToolCall.created_at, AgentToolCall.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_actions_for_run(self, *, run_id: UUID) -> list[AgentAction]:
        statement = select(AgentAction).where(AgentAction.run_id == run_id).order_by(AgentAction.created_at, AgentAction.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_artifacts_for_run(self, *, run_id: UUID) -> list[AgentArtifact]:
        statement = select(AgentArtifact).where(AgentArtifact.run_id == run_id).order_by(AgentArtifact.created_at, AgentArtifact.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_latest_artifact_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        artifact_type: str,
    ) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.actor_user_id == owner_user_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentArtifact.artifact_type == artifact_type,
                AgentArtifact.status != "deleted",
            )
            .order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc())
            .limit(1)
        )
        return cast(AgentArtifact | None, await self.session.scalar(statement))

    async def get_artifact_for_owner(self, *, artifact_id: UUID, owner_user_id: UUID) -> AgentArtifact | None:
        statement = (
            select(AgentArtifact)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentArtifact.id == artifact_id,
                AgentArtifact.owner_user_id == owner_user_id,
                AgentThread.owner_user_id == owner_user_id,
                AgentThread.deleted_at.is_(None),
            )
        )
        return cast(AgentArtifact | None, await self.session.scalar(statement))

    async def create_artifact(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        artifact_type: str,
        schema_version: str,
        status: str,
        payload: dict[str, Any],
        raw_payload_ref: str = "",
    ) -> AgentArtifact:
        artifact = AgentArtifact(
            run_id=run_id,
            owner_user_id=owner_user_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            status=status,
            payload=payload,
            raw_payload_ref=raw_payload_ref,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def mark_artifact_deleted(self, *, artifact: AgentArtifact) -> AgentArtifact:
        artifact.status = "deleted"
        await self.session.flush()
        return artifact

    async def create_context_checkpoint(
        self,
        *,
        thread_id: UUID,
        run_id: UUID | None,
        checkpoint_namespace: str,
        checkpoint_id: str,
        graph_version: str,
        state_ref: str,
        state_summary: dict[str, Any],
    ) -> AgentContextCheckpoint:
        checkpoint = AgentContextCheckpoint(
            thread_id=thread_id,
            run_id=run_id,
            checkpoint_namespace=checkpoint_namespace,
            checkpoint_id=checkpoint_id,
            graph_version=graph_version,
            state_ref=state_ref,
            state_summary=state_summary,
        )
        self.session.add(checkpoint)
        await self.session.flush()
        return checkpoint

    async def get_context_checkpoint(
        self,
        *,
        checkpoint_namespace: str,
        checkpoint_id: str,
    ) -> AgentContextCheckpoint | None:
        statement = select(AgentContextCheckpoint).where(
            AgentContextCheckpoint.checkpoint_namespace == checkpoint_namespace,
            AgentContextCheckpoint.checkpoint_id == checkpoint_id,
        )
        return cast(AgentContextCheckpoint | None, await self.session.scalar(statement))

    async def get_latest_context_checkpoint_for_thread(
        self,
        *,
        thread_id: UUID,
        checkpoint_namespace: str,
    ) -> AgentContextCheckpoint | None:
        statement = (
            select(AgentContextCheckpoint)
            .where(
                AgentContextCheckpoint.thread_id == thread_id,
                AgentContextCheckpoint.checkpoint_namespace == checkpoint_namespace,
            )
            .order_by(AgentContextCheckpoint.created_at.desc(), AgentContextCheckpoint.id.desc())
            .limit(1)
        )
        return cast(AgentContextCheckpoint | None, await self.session.scalar(statement))

    async def get_latest_context_checkpoint_for_run(self, *, run_id: UUID) -> AgentContextCheckpoint | None:
        statement = (
            select(AgentContextCheckpoint)
            .where(AgentContextCheckpoint.run_id == run_id)
            .order_by(AgentContextCheckpoint.created_at.desc(), AgentContextCheckpoint.id.desc())
            .limit(1)
        )
        return cast(AgentContextCheckpoint | None, await self.session.scalar(statement))

    async def list_context_checkpoints_for_run(self, *, run_id: UUID) -> list[AgentContextCheckpoint]:
        statement = select(AgentContextCheckpoint).where(AgentContextCheckpoint.run_id == run_id).order_by(
            AgentContextCheckpoint.created_at,
            AgentContextCheckpoint.id,
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_workflow_state(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        run_id: UUID | None,
        workflow_type: str,
        status: str,
        schema_version: str,
        state: dict[str, Any],
        active_step: str,
        revision: int,
        step_token: str,
        expires_at: datetime | None,
    ) -> AgentWorkflowState:
        workflow_state = AgentWorkflowState(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            workflow_type=workflow_type,
            status=status,
            schema_version=schema_version,
            state=state,
            active_step=active_step,
            revision=revision,
            step_token=step_token,
            expires_at=expires_at,
        )
        self.session.add(workflow_state)
        await self.session.flush()
        return workflow_state

    async def update_workflow_state(
        self,
        *,
        workflow_state: AgentWorkflowState,
        status: str | None = None,
        state: dict[str, Any] | None = None,
        active_step: str | None = None,
        revision: int | None = None,
        step_token: str | None = None,
        completed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> AgentWorkflowState:
        if status is not None:
            workflow_state.status = status
        if state is not None:
            workflow_state.state = state
        if active_step is not None:
            workflow_state.active_step = active_step
        if revision is not None:
            workflow_state.revision = revision
        if step_token is not None:
            workflow_state.step_token = step_token
        if completed_at is not None:
            workflow_state.completed_at = completed_at
        if expires_at is not None:
            workflow_state.expires_at = expires_at
        await self.session.flush()
        return workflow_state

    async def get_workflow_state_for_owner(
        self,
        *,
        workflow_state_id: UUID,
        owner_user_id: UUID,
    ) -> AgentWorkflowState | None:
        statement = select(AgentWorkflowState).where(
            AgentWorkflowState.id == workflow_state_id,
            AgentWorkflowState.owner_user_id == owner_user_id,
        )
        return cast(AgentWorkflowState | None, await self.session.scalar(statement))

    async def get_latest_workflow_state_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        workflow_type: str,
    ) -> AgentWorkflowState | None:
        statement = (
            select(AgentWorkflowState)
            .where(
                AgentWorkflowState.thread_id == thread_id,
                AgentWorkflowState.owner_user_id == owner_user_id,
                AgentWorkflowState.workflow_type == workflow_type,
            )
            .order_by(AgentWorkflowState.updated_at.desc(), AgentWorkflowState.id.desc())
            .limit(1)
        )
        return cast(AgentWorkflowState | None, await self.session.scalar(statement))

    async def list_active_workflow_states_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int = 5,
    ) -> list[AgentWorkflowState]:
        statement = (
            select(AgentWorkflowState)
            .where(
                AgentWorkflowState.thread_id == thread_id,
                AgentWorkflowState.owner_user_id == owner_user_id,
                AgentWorkflowState.status.in_(("collecting", "ready", "waiting", "paused")),
                or_(AgentWorkflowState.expires_at.is_(None), AgentWorkflowState.expires_at > func.now()),
            )
            .order_by(AgentWorkflowState.updated_at.desc(), AgentWorkflowState.id.desc())
            .limit(max(1, min(int(limit), 20)))
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_workflow_states_for_run(self, *, run_id: UUID) -> list[AgentWorkflowState]:
        statement = select(AgentWorkflowState).where(AgentWorkflowState.run_id == run_id).order_by(
            AgentWorkflowState.updated_at,
            AgentWorkflowState.id,
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_context_projection(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        context_schema_version: str,
        prompt_version: str,
        tool_schema_version: str,
        selected_message_ids: list[Any],
        active_workflow_state_id: UUID | None,
        source_refs: dict[str, Any],
        projection_summary: dict[str, Any],
        token_estimate: int,
    ) -> AgentContextProjection:
        projection = AgentContextProjection(
            run_id=run_id,
            thread_id=thread_id,
            context_schema_version=context_schema_version,
            prompt_version=prompt_version,
            tool_schema_version=tool_schema_version,
            selected_message_ids=selected_message_ids,
            active_workflow_state_id=active_workflow_state_id,
            source_refs=source_refs,
            projection_summary=projection_summary,
            token_estimate=token_estimate,
        )
        self.session.add(projection)
        await self.session.flush()
        return projection

    async def get_latest_context_projection_for_run(self, *, run_id: UUID) -> AgentContextProjection | None:
        statement = (
            select(AgentContextProjection)
            .where(AgentContextProjection.run_id == run_id)
            .order_by(AgentContextProjection.created_at.desc(), AgentContextProjection.id.desc())
            .limit(1)
        )
        return cast(AgentContextProjection | None, await self.session.scalar(statement))

    async def list_context_projections_for_run(self, *, run_id: UUID) -> list[AgentContextProjection]:
        statement = select(AgentContextProjection).where(AgentContextProjection.run_id == run_id).order_by(
            AgentContextProjection.created_at,
            AgentContextProjection.id,
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def upsert_run_summary(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
        service_skill_id: str,
        summary_type: str,
        schema_version: str,
        payload: dict[str, Any],
        source_message_ids: list[Any],
        source_tool_call_ids: list[Any],
    ) -> AgentRunSummary:
        statement = select(AgentRunSummary).where(
            AgentRunSummary.run_id == run_id,
            AgentRunSummary.summary_type == summary_type,
        )
        summary = cast(AgentRunSummary | None, await self.session.scalar(statement))
        if summary is None:
            summary = AgentRunSummary(
                run_id=run_id,
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                service_skill_id=service_skill_id,
                summary_type=summary_type,
                schema_version=schema_version,
                payload=payload,
                source_message_ids=source_message_ids,
                source_tool_call_ids=source_tool_call_ids,
            )
            self.session.add(summary)
        else:
            summary.service_skill_id = service_skill_id
            summary.schema_version = schema_version
            summary.payload = payload
            summary.source_message_ids = source_message_ids
            summary.source_tool_call_ids = source_tool_call_ids
        await self.session.flush()
        return summary

    async def list_recent_run_summaries(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int,
        summary_type: str = "run_fact",
        exclude_run_id: UUID | None = None,
    ) -> list[AgentRunSummary]:
        conditions = [
            AgentRunSummary.thread_id == thread_id,
            AgentRunSummary.owner_user_id == owner_user_id,
            AgentRunSummary.summary_type == summary_type,
        ]
        if exclude_run_id is not None:
            conditions.append(AgentRunSummary.run_id != exclude_run_id)
        statement = (
            select(AgentRunSummary)
            .where(*conditions)
            .order_by(AgentRunSummary.created_at.desc(), AgentRunSummary.id.desc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        summaries = list(result.all())
        summaries.reverse()
        return summaries

    async def list_safety_events_for_run(self, *, run_id: UUID) -> list[AgentSafetyEvent]:
        statement = select(AgentSafetyEvent).where(AgentSafetyEvent.run_id == run_id).order_by(AgentSafetyEvent.created_at, AgentSafetyEvent.id)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def fail_tool_call(
        self,
        *,
        tool_call: AgentToolCall,
        completed_at: datetime,
        error_code: str,
    ) -> AgentToolCall:
        tool_call.status = "failed"
        tool_call.completed_at = completed_at
        tool_call.error_code = error_code
        await self.session.flush()
        return tool_call

    async def create_tool_output(
        self,
        *,
        tool_call_id: UUID,
        safe_output: dict[str, Any],
        raw_output_ref: str = "",
    ) -> AgentToolOutput:
        output = AgentToolOutput(tool_call_id=tool_call_id, safe_output=safe_output, raw_output_ref=raw_output_ref)
        self.session.add(output)
        await self.session.flush()
        return output

    async def list_tool_outputs_for_run(self, *, run_id: UUID) -> list[tuple[AgentToolCall, AgentToolOutput]]:
        statement = (
            select(AgentToolCall, AgentToolOutput)
            .join(AgentToolOutput, AgentToolOutput.tool_call_id == AgentToolCall.id)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at, AgentToolCall.id, AgentToolOutput.created_at, AgentToolOutput.id)
        )
        result = await self.session.execute(statement)
        return [(cast(AgentToolCall, call), cast(AgentToolOutput, output)) for call, output in result.all()]

    async def record_safety_event(
        self,
        *,
        run_id: UUID | None,
        owner_user_id: UUID,
        category: str,
        severity: str,
        decision: str,
        evidence: dict[str, Any],
        evidence_ref: str = "",
    ) -> AgentSafetyEvent:
        event = AgentSafetyEvent(
            run_id=run_id,
            owner_user_id=owner_user_id,
            category=category,
            severity=severity,
            decision=decision,
            evidence=evidence,
            evidence_ref=evidence_ref,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def create_eval_case(
        self,
        *,
        suite: str,
        name: str,
        domain: str,
        input_payload: dict[str, Any],
        expected_behavior: dict[str, Any],
        expected_tool_calls: list[Any],
        expected_safety_decision: str,
        source_run_id: UUID | None,
        status: str,
        owner_team: str,
    ) -> AgentEvalCase:
        eval_case = AgentEvalCase(
            suite=suite,
            name=name,
            domain=domain,
            input_payload=input_payload,
            expected_behavior=expected_behavior,
            expected_tool_calls=expected_tool_calls,
            expected_safety_decision=expected_safety_decision,
            source_run_id=source_run_id,
            status=status,
            owner_team=owner_team,
        )
        self.session.add(eval_case)
        await self.session.flush()
        return eval_case

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

    async def mark_run_failed(
        self,
        *,
        run: AgentRun,
        completed_at: datetime,
        error_code: str,
        error_details: dict[str, Any],
    ) -> AgentRun:
        run.status = "failed"
        run.completed_at = completed_at
        run.error_code = error_code
        run.error_details = error_details
        await self.session.flush()
        return run

    async def _next_thread_message_sequence(self, *, thread_id: UUID) -> int:
        statement = select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(AgentMessage.thread_id == thread_id)
        return int(await self.session.scalar(statement) or 1)

    async def _next_run_event_sequence(self, *, run_id: UUID) -> int:
        statement = select(func.coalesce(func.max(AgentEvent.sequence), 0) + 1).where(AgentEvent.run_id == run_id)
        return int(await self.session.scalar(statement) or 1)
