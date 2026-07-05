from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from ..models import AgentRun


AgentRunExecutionStatus = Literal["completed", "waiting_for_confirmation"]


@dataclass(frozen=True)
class AgentRunExecutionResult:
    status: AgentRunExecutionStatus
    final_text: str = ""
    pending_action_id: UUID | None = None


AgentRunHandler = Callable[[AgentRun], Awaitable[AgentRunExecutionResult]]
