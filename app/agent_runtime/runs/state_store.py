from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from app.agent_runtime.context.workflow_reply import new_workflow_step_token

from .models import AgentWorkflowState
from .repository import AgentRuntimeRepository


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
        transition_metadata: dict[str, Any] | None = None,
    ) -> AgentWorkflowState:
        workflow = await self.repository.create_workflow_state(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            workflow_type=workflow_type,
            status=status,
            schema_version=schema_version,
            state=_json_compatible(state),
            active_step=active_step,
            revision=1,
            step_token=new_workflow_step_token(),
            expires_at=expires_at,
        )
        await self._append_transition_event(
            workflow=workflow,
            interaction_thread_id=thread_id,
            run_id=run_id,
            before=None,
            transition_metadata=transition_metadata,
        )
        return workflow

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
        lookup_scope: Literal["thread", "owner"] = "thread",
        transition_metadata: dict[str, Any] | None = None,
    ) -> AgentWorkflowState:
        if lookup_scope == "owner":
            owner_lock = getattr(self.repository, "lock_workflow_owner", None)
            if callable(owner_lock):
                await owner_lock(owner_user_id=owner_user_id)
            workflow = await self.repository.get_latest_workflow_state_for_owner(
                owner_user_id=owner_user_id,
                workflow_type=workflow_type,
                for_update=True,
            )
        elif lookup_scope == "thread":
            workflow = await self.repository.get_latest_workflow_state_for_thread(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                workflow_type=workflow_type,
                for_update=True,
            )
        else:
            raise ValueError("lookup_scope must be 'thread' or 'owner'")
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
                transition_metadata=transition_metadata,
            )
        before = _workflow_snapshot(workflow)
        workflow.run_id = run_id
        workflow.schema_version = schema_version
        current_revision = getattr(workflow, "revision", None)
        next_revision = (current_revision if isinstance(current_revision, int) and current_revision > 0 else 0) + 1
        updated = await self.repository.update_workflow_state(
            workflow_state=workflow,
            status=status,
            state=_json_compatible(state),
            active_step=active_step,
            revision=next_revision,
            step_token=new_workflow_step_token(),
            expires_at=expires_at,
            clear_expires_at=(
                lookup_scope == "owner"
                and workflow_type == "pregnancy_plan"
                and expires_at is None
            ),
        )
        await self._append_transition_event(
            workflow=updated,
            interaction_thread_id=thread_id,
            run_id=run_id,
            before=before,
            transition_metadata=transition_metadata,
        )
        return updated

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

    async def _append_transition_event(
        self,
        *,
        workflow: AgentWorkflowState,
        interaction_thread_id: UUID,
        run_id: UUID | None,
        before: dict[str, Any] | None,
        transition_metadata: dict[str, Any] | None,
    ) -> None:
        append_event = getattr(self.repository, "append_workflow_event", None)
        if not callable(append_event):
            return
        metadata = _json_compatible(transition_metadata or {})
        event_type = str(metadata.pop("event_type", "") or "").strip()[:120]
        if not event_type:
            event_type = "workflow.created" if before is None else "workflow.transitioned"
        after = _workflow_snapshot(workflow)
        await append_event(
            workflow_state_id=workflow.id,
            owner_user_id=workflow.owner_user_id,
            thread_id=interaction_thread_id,
            run_id=run_id,
            workflow_type=workflow.workflow_type,
            event_type=event_type,
            from_revision=int(before.get("revision") or 0) if before else 0,
            to_revision=int(after.get("revision") or 0),
            payload={
                **metadata,
                "before": before,
                "after": after,
            },
        )


def _workflow_snapshot(workflow: AgentWorkflowState) -> dict[str, Any]:
    return {
        "status": str(workflow.status or ""),
        "active_step": str(workflow.active_step or ""),
        "revision": int(workflow.revision or 0),
        "state": _json_compatible(workflow.state if isinstance(workflow.state, dict) else {}),
    }

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
