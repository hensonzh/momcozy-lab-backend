from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from ..models import AgentContextProjection, AgentRun, AgentWorkflowState
from ..repository import AgentRuntimeRepository


class AgentRuntimeStateStore:
    def __init__(self, *, repository: AgentRuntimeRepository) -> None:
        self.repository = repository

    async def create_workflow_state(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        workflow_type: str,
        state: dict[str, Any],
        run_id: UUID | None = None,
        status: str = "collecting",
        schema_version: str = "v1",
        active_step: str = "",
        expires_at: datetime | None = None,
    ) -> AgentWorkflowState:
        return await self.repository.create_workflow_state(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            workflow_type=workflow_type,
            status=status,
            schema_version=schema_version,
            state=_json_compatible(state),
            active_step=active_step,
            expires_at=expires_at,
        )

    async def upsert_active_workflow(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        workflow_type: str,
        state: dict[str, Any],
        run_id: UUID | None = None,
        status: str = "collecting",
        schema_version: str = "v1",
        active_step: str = "",
        expires_at: datetime | None = None,
    ) -> AgentWorkflowState:
        workflow = await self.repository.get_latest_workflow_state_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            workflow_type=workflow_type,
        )
        if workflow is None or workflow.status in {"completed", "expired", "failed"}:
            return await self.create_workflow_state(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                workflow_type=workflow_type,
                state=state,
                run_id=run_id,
                status=status,
                schema_version=schema_version,
                active_step=active_step,
                expires_at=expires_at,
            )
        workflow.run_id = run_id
        workflow.schema_version = schema_version
        return await self.repository.update_workflow_state(
            workflow_state=workflow,
            status=status,
            state=_json_compatible(state),
            active_step=active_step,
            expires_at=expires_at,
        )

    async def list_active_workflows(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int = 5,
    ) -> list[AgentWorkflowState]:
        return await self.repository.list_active_workflow_states_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            limit=limit,
        )

    async def record_context_projection(
        self,
        *,
        run: AgentRun,
        selected_message_ids: list[UUID],
        source_refs: dict[str, Any],
        projection_summary: dict[str, Any],
        active_workflow_state_id: UUID | None = None,
        context_schema_version: str = "v1",
        tool_schema_version: str = "v1",
        token_estimate: int = 0,
    ) -> AgentContextProjection:
        return await self.repository.create_context_projection(
            run_id=run.id,
            thread_id=run.thread_id,
            context_schema_version=context_schema_version,
            prompt_version=run.prompt_version,
            tool_schema_version=tool_schema_version,
            selected_message_ids=[str(message_id) for message_id in selected_message_ids],
            active_workflow_state_id=active_workflow_state_id,
            source_refs=_json_compatible(source_refs),
            projection_summary=_json_compatible(projection_summary),
            token_estimate=max(0, int(token_estimate)),
        )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
