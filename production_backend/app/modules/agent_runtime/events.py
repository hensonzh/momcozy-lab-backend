from __future__ import annotations

from typing import Any
from uuid import UUID

from .controls import AgentRunControls
from .models import AgentEvent
from .repository import AgentRuntimeRepository


class AgentEventSink:
    def __init__(self, *, repository: AgentRuntimeRepository, controls: AgentRunControls | None = None) -> None:
        self.repository = repository
        self.controls = controls

    async def append_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        event = await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run_id, sequence=event.sequence)
        return event
