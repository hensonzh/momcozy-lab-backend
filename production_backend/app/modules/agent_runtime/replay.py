from __future__ import annotations

from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from .models import AgentAction, AgentEvent, AgentMessage, AgentRun, AgentSafetyEvent, AgentToolCall
from .repository import AgentRuntimeRepository


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
        safety_events = await self.repository.list_safety_events_for_run(run_id=run.id)
        return {
            "run": _run(run),
            "messages": [_message(message, include_content=include_message_content) for message in messages],
            "events": [_event(event) for event in events],
            "tool_calls": [_tool_call(tool_call) for tool_call in tool_calls],
            "actions": [_action(action) for action in actions],
            "safety_events": [_safety_event(safety_event) for safety_event in safety_events],
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
        "error_details": run.error_details,
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
    body["content"] = message.content if include_content else {"redacted": True}
    return body


def _event(event: AgentEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.event_id),
        "sequence": event.sequence,
        "type": event.event_type,
        "payload": event.payload,
    }


def _tool_call(tool_call: AgentToolCall) -> dict[str, Any]:
    return {
        "id": str(tool_call.id),
        "tool_name": tool_call.tool_name,
        "call_id": tool_call.call_id,
        "status": tool_call.status,
        "safe_args": tool_call.safe_args,
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
        "preview_payload": action.preview_payload,
        "error_code": action.error_code,
    }


def _safety_event(safety_event: AgentSafetyEvent) -> dict[str, Any]:
    return {
        "id": str(safety_event.id),
        "category": safety_event.category,
        "severity": safety_event.severity,
        "decision": safety_event.decision,
        "evidence": safety_event.evidence,
        "evidence_ref": safety_event.evidence_ref,
    }
