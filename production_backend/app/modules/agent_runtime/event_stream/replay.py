from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from ....core.errors import ApiError
from ..models import (
    AgentAction,
    AgentArtifact,
    AgentContextCheckpoint,
    AgentContextProjection,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentWorkflowState,
)
from ..repository import AgentRuntimeRepository


class AgentReplayService:
    def __init__(self, *, repository: AgentRuntimeRepository) -> None:
        self.repository = repository

    async def export_run_bundle(self, *, run_id: UUID, include_message_content: bool = False) -> dict[str, Any]:
        run = await self.repository.get_run(run_id=run_id)
        if run is None:
            raise ApiError(code="not_found", message="Agent run not found.", status=404)
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id)
        events = await self.repository.list_events_for_run(run_id=run.id)
        tool_calls = await self.repository.list_tool_calls_for_run(run_id=run.id)
        actions = await self.repository.list_actions_for_run(run_id=run.id)
        artifacts = await self.repository.list_artifacts_for_run(run_id=run.id)
        checkpoints = await self.repository.list_context_checkpoints_for_run(run_id=run.id)
        workflow_states = await self.repository.list_workflow_states_for_run(run_id=run.id)
        context_projections = await self.repository.list_context_projections_for_run(run_id=run.id)
        return {
            "run": _run(run),
            "messages": [_message(message, include_content=include_message_content) for message in messages],
            "events": [_event(event) for event in events],
            "tool_calls": [_tool_call(tool_call) for tool_call in tool_calls],
            "actions": [_action(action) for action in actions],
            "artifacts": [_artifact(artifact) for artifact in artifacts],
            "checkpoints": [_checkpoint(checkpoint) for checkpoint in checkpoints],
            "workflow_states": [_workflow_state(workflow_state) for workflow_state in workflow_states],
            "context_projections": [_context_projection(context_projection) for context_projection in context_projections],
        }


def _run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "thread_id": str(run.thread_id),
        "actor_user_id": str(run.actor_user_id),
        "status": run.status,
        "runtime_pattern": run.runtime_pattern,
        "graph_version": run.graph_version,
        "prompt_version": run.prompt_version,
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


def _pregnancy_plan_workflow_replay_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    projected = {
        key: _redact_replay_value(state.get(key))
        for key in (
            "phase",
            "form_id",
            "source_form_artifact_id",
            "source_form_submission_id",
            "analysis_run_id",
            "consumed_by_action_id",
            "interrupted_by_safety_signal",
        )
        if state.get(key) not in (None, "")
    }
    analysis = state.get("analysis")
    focuses = analysis.get("focuses") if isinstance(analysis, dict) else None
    if isinstance(focuses, list):
        focus_count = sum(1 for focus in focuses if isinstance(focus, dict) and str(focus.get("id") or "").strip())
        projected["focus_count"] = focus_count
        projected["personalized"] = focus_count > 1
    return projected


def _checkpoint(checkpoint: AgentContextCheckpoint) -> dict[str, Any]:
    return {
        "id": str(checkpoint.id),
        "thread_id": str(checkpoint.thread_id),
        "run_id": str(checkpoint.run_id) if checkpoint.run_id else None,
        "checkpoint_namespace": checkpoint.checkpoint_namespace,
        "checkpoint_id": checkpoint.checkpoint_id,
        "graph_version": checkpoint.graph_version,
        "state_ref": checkpoint.state_ref,
        "state_summary": _redact_replay_value(checkpoint.state_summary),
        "created_at": checkpoint.created_at.isoformat() if checkpoint.created_at else None,
    }


def _workflow_state(workflow_state: AgentWorkflowState) -> dict[str, Any]:
    state = (
        _pregnancy_plan_workflow_replay_state(workflow_state.state)
        if workflow_state.workflow_type == "pregnancy_plan"
        else _redact_replay_value(workflow_state.state)
    )
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


def _context_projection(context_projection: AgentContextProjection) -> dict[str, Any]:
    return {
        "id": str(context_projection.id),
        "run_id": str(context_projection.run_id),
        "thread_id": str(context_projection.thread_id),
        "context_schema_version": context_projection.context_schema_version,
        "prompt_version": context_projection.prompt_version,
        "tool_schema_version": context_projection.tool_schema_version,
        "selected_message_ids": context_projection.selected_message_ids,
        "active_workflow_state_id": str(context_projection.active_workflow_state_id)
        if context_projection.active_workflow_state_id
        else None,
        "source_refs": _redact_replay_value(context_projection.source_refs),
        "projection_summary": _redact_replay_value(context_projection.projection_summary),
        "token_estimate": context_projection.token_estimate,
        "created_at": context_projection.created_at.isoformat() if context_projection.created_at else None,
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
