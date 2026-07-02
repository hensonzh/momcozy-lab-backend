from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AgentAction,
    AgentArtifact,
    AgentContextCheckpoint,
    AgentContextProjection,
    AgentEvent,
    AgentEvalCase,
    AgentMessage,
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
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def get_run(self, *, run_id: UUID) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id).execution_options(populate_existing=True)
        return cast(AgentRun | None, await self.session.scalar(statement))

    async def refresh_run(self, *, run: AgentRun) -> AgentRun:
        await self.session.refresh(run)
        return run

    async def list_runnable_runs(
        self,
        *,
        limit: int,
        recover_running_before: datetime | None = None,
    ) -> list[AgentRun]:
        conditions = [AgentRun.status == "queued"]
        if recover_running_before is not None:
            conditions.append(
                and_(
                    AgentRun.status == "running",
                    or_(AgentRun.started_at.is_(None), AgentRun.started_at <= recover_running_before),
                )
            )
        statement = select(AgentRun).where(or_(*conditions)).order_by(AgentRun.created_at.asc(), AgentRun.id.asc()).limit(limit)
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
        return cast(AgentAction | None, await self.session.scalar(statement))

    async def get_action(self, *, action_id: UUID) -> AgentAction | None:
        statement = select(AgentAction).where(AgentAction.id == action_id)
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
        completed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> AgentWorkflowState:
        if status is not None:
            workflow_state.status = status
        if state is not None:
            workflow_state.state = state
        if active_step is not None:
            workflow_state.active_step = active_step
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
