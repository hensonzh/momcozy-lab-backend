from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class AgentGraphState(TypedDict):
    run_id: str
    thread_id: str
    actor_user_id: str
    current_user_message_id: str
    context_refs: list[str]
    pending_action_id: str | None
    final_message_id: str | None
    current_step: NotRequired[str]
    visited_nodes: NotRequired[list[str]]
    outcome_status: NotRequired[Literal["completed", "waiting_for_confirmation"]]
    final_text: NotRequired[str]
    assistant_message_id: NotRequired[str | None]
    quick_replies: NotRequired[list[dict[str, Any]]]
    workflow_reply: NotRequired[dict[str, Any]]
    stream_segment_count: NotRequired[int]
    cancellation_requested: NotRequired[bool]
