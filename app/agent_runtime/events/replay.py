from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from app.agent_runtime.runs.models import (
    AgentAction,
    AgentArtifact,
    AgentContextItem,
    AgentEvent,
    AgentMessage,
    AgentModelContextSnapshot,
    AgentRun,
    AgentToolCall,
    AgentWorkflowEvent,
    AgentWorkflowState,
)
from app.agent_runtime.runs.repository import AgentRuntimeRepository


class AgentReplayService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        workflow_state_projector: Callable[[AgentWorkflowState], dict[str, Any] | None] | None = None,
    ) -> None:
        self.repository = repository
        self.workflow_state_projector = workflow_state_projector

    async def export_run_bundle(self, *, run_id: UUID, include_message_content: bool = False) -> dict[str, Any]:
        run = await self.repository.get_run(run_id=run_id)
        if run is None:
            raise ApiError(code="not_found", message="Agent run not found.", status=404)
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id)
        context_items = await self.repository.list_context_items_for_thread(thread_id=run.thread_id)
        events = await self.repository.list_events_for_run(run_id=run.id)
        tool_calls = await self.repository.list_tool_calls_for_run(run_id=run.id)
        actions = await self.repository.list_actions_for_run(run_id=run.id)
        artifacts = await self.repository.list_artifacts_for_run(run_id=run.id)
        workflow_states = await self.repository.list_workflow_states_for_run(run_id=run.id)
        workflow_event_loader = getattr(self.repository, "list_workflow_events_for_run", None)
        workflow_events = (
            await workflow_event_loader(run_id=run.id)
            if callable(workflow_event_loader)
            else []
        )
        context_snapshot_loader = getattr(
            self.repository,
            "list_model_context_snapshots_for_run",
            None,
        )
        context_snapshots = (
            await context_snapshot_loader(run_id=run.id)
            if callable(context_snapshot_loader)
            else []
        )
        return {
            "run": _run(run),
            "messages": [_message(message, include_content=include_message_content) for message in messages],
            "context_items": [
                _context_item(context_item, include_content=include_message_content)
                for context_item in context_items
            ],
            "events": [_event(event) for event in events],
            "tool_calls": [_tool_call(tool_call) for tool_call in tool_calls],
            "actions": [_action(action) for action in actions],
            "artifacts": [_artifact(artifact) for artifact in artifacts],
            "checkpoints": [],
            "workflow_states": [
                _workflow_state(workflow_state, projector=self.workflow_state_projector)
                for workflow_state in workflow_states
            ],
            "workflow_events": [
                _workflow_event(
                    workflow_event,
                    include_content=include_message_content,
                )
                for workflow_event in workflow_events
            ],
            "model_context_snapshots": [
                _model_context_snapshot(
                    snapshot,
                    include_content=include_message_content,
                )
                for snapshot in context_snapshots
            ],
        }


def _run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "thread_id": str(run.thread_id),
        "actor_user_id": str(run.actor_user_id),
        "status": run.status,
        "runtime_pattern": run.runtime_pattern,
        "runtime_version": run.runtime_version,
        "request_id": run.request_id,
        "trace_id": run.trace_id,
        "error_code": run.error_code,
        "error_details": _redact_replay_value(run.error_details),
    }


def _message(message: AgentMessage, *, include_content: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(message.id),
        "run_id": str(message.run_id) if message.run_id else "",
        "role": message.role,
        "message_type": message.message_type,
        "status": message.status,
        "sequence": message.sequence,
    }
    body["content"] = _redact_replay_value(message.content) if include_content else {"redacted": True}
    return body


def _context_item(context_item: AgentContextItem, *, include_content: bool) -> dict[str, Any]:
    return {
        "id": str(context_item.id),
        "run_id": str(context_item.run_id) if context_item.run_id else "",
        "item_key": context_item.item_key,
        "item_type": context_item.item_type,
        "sequence": context_item.sequence,
        "item": _redact_replay_value(context_item.item) if include_content else {"redacted": True},
        "created_at": context_item.created_at.isoformat() if context_item.created_at else None,
    }


def _event(event: AgentEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.event_id),
        "thread_id": str(event.thread_id),
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "type": event.event_type,
        "payload": _redact_replay_value(event.payload),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _tool_call(tool_call: AgentToolCall) -> dict[str, Any]:
    return {
        "id": str(tool_call.id),
        "tool_name": tool_call.tool_name,
        "call_id": tool_call.call_id,
        "status": tool_call.status,
        "safe_args": _redact_replay_value(tool_call.safe_args),
        "error_code": tool_call.error_code,
    }


def _action(action: AgentAction) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
        "status": action.status,
        "side_effect_level": action.side_effect_level,
        "preview_payload": _redact_replay_value(action.preview_payload),
        "error_code": action.error_code,
    }


def _artifact(artifact: AgentArtifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "run_id": str(artifact.run_id),
        "owner_user_id": str(artifact.owner_user_id),
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "status": artifact.status,
        "payload": _redact_replay_value(artifact.payload),
        "raw_payload_ref": artifact.raw_payload_ref,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def _workflow_state(
    workflow_state: AgentWorkflowState,
    *,
    projector: Callable[[AgentWorkflowState], dict[str, Any] | None] | None,
) -> dict[str, Any]:
    projected = projector(workflow_state) if projector is not None else None
    state = _redact_replay_value(projected if projected is not None else workflow_state.state)
    return {
        "id": str(workflow_state.id),
        "thread_id": str(workflow_state.thread_id),
        "owner_user_id": str(workflow_state.owner_user_id),
        "run_id": str(workflow_state.run_id) if workflow_state.run_id else None,
        "workflow_type": workflow_state.workflow_type,
        "status": workflow_state.status,
        "schema_version": workflow_state.schema_version,
        "active_step": workflow_state.active_step,
        "state": state,
        "created_at": workflow_state.created_at.isoformat() if workflow_state.created_at else None,
        "updated_at": workflow_state.updated_at.isoformat() if workflow_state.updated_at else None,
    }


def _workflow_event(
    workflow_event: AgentWorkflowEvent,
    *,
    include_content: bool,
) -> dict[str, Any]:
    payload = _redact_replay_value(workflow_event.payload)
    if not include_content and isinstance(payload, dict):
        payload = dict(payload)
        for boundary in ("before", "after"):
            snapshot = payload.get(boundary)
            if isinstance(snapshot, dict) and "state" in snapshot:
                payload[boundary] = {
                    **snapshot,
                    "state": {"redacted": True},
                }
        interaction = payload.get("interaction")
        if isinstance(interaction, dict) and "answer" in interaction:
            payload["interaction"] = {
                **interaction,
                "answer": "[redacted]",
            }
    return {
        "id": str(workflow_event.id),
        "workflow_state_id": str(workflow_event.workflow_state_id),
        "thread_id": str(workflow_event.thread_id),
        "run_id": str(workflow_event.run_id) if workflow_event.run_id else None,
        "workflow_type": workflow_event.workflow_type,
        "sequence": workflow_event.sequence,
        "event_type": workflow_event.event_type,
        "from_revision": workflow_event.from_revision,
        "to_revision": workflow_event.to_revision,
        "payload": payload,
        "created_at": workflow_event.created_at.isoformat() if workflow_event.created_at else None,
    }


def _model_context_snapshot(
    snapshot: AgentModelContextSnapshot,
    *,
    include_content: bool,
) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "run_id": str(snapshot.run_id),
        "thread_id": str(snapshot.thread_id),
        "owner_user_id": str(snapshot.owner_user_id),
        "sequence": snapshot.sequence,
        "schema_version": snapshot.schema_version,
        "item_refs": _redact_replay_value(snapshot.item_refs),
        "dynamic_context": (
            _redact_replay_value(snapshot.dynamic_context)
            if include_content
            else {"redacted": True}
        ),
        "selection_policy": _redact_replay_value(snapshot.selection_policy),
        "input_item_count": snapshot.input_item_count,
        "estimated_input_tokens": snapshot.estimated_input_tokens,
        "model_input_sha256": snapshot.model_input_sha256,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


SENSITIVE_REPLAY_KEYS = (
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "access_key",
    "refresh",
    "email",
    "phone",
    "contact",
    "address",
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _redact_replay_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _is_sensitive_replay_key(str(key)) else _redact_replay_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_replay_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_replay_value(item) for item in value]
    if isinstance(value, str):
        return "[redacted]" if EMAIL_RE.search(value) or PHONE_RE.search(value) else value
    return value


def _is_sensitive_replay_key(key: str) -> bool:
    normalized = key.lower()
    return any(term in normalized for term in SENSITIVE_REPLAY_KEYS)
