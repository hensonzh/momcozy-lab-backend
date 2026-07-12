from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from ..models import AgentRun


AgentRunExecutionStatus = Literal["completed", "waiting_for_confirmation"]


@dataclass(frozen=True)
class AgentRunExecutionResult:
    status: AgentRunExecutionStatus
    final_text: str = ""
    pending_action_id: UUID | None = None
    completed_action_id: UUID | None = None
    completion_reason: str = ""
    assistant_message_id: UUID | None = None
    quick_replies: list[dict[str, Any]] = field(default_factory=list)
    stream_segment_count: int = 0


AgentRunHandler = Callable[[AgentRun], Awaitable[AgentRunExecutionResult]]
