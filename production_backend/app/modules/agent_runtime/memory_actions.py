from __future__ import annotations

from typing import Any

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from .action_outbox import AgentActionApplyResult
from .memory import AgentMemoryService
from .models import AgentAction


AGENT_MEMORY_CREATE_ACTION = "agent.memory.create"


class AgentMemoryCreateActionHandler:
    def __init__(self, *, service: AgentMemoryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        memory_type = _text(payload, "memory_type")
        if not memory_type:
            raise PermanentJobError("missing_memory_type")
        content = payload.get("content")
        if not isinstance(content, dict) or not content:
            raise PermanentJobError("missing_memory_content")
        confidence_score = _confidence_score(payload.get("confidence_score", 0))

        try:
            memory = await self.service.create_memory(
                owner_user_id=action.actor_user_id,
                memory_type=memory_type,
                content=content,
                source_run_id=action.run_id,
                confidence_score=confidence_score,
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="agent_memory",
            resource_id=str(memory.id),
            details={
                "memory_type": memory.memory_type,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _confidence_score(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PermanentJobError("invalid_confidence_score")
    return value
