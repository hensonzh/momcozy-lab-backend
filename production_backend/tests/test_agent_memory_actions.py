import asyncio
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.memory_actions import AGENT_MEMORY_CREATE_ACTION, AgentMemoryCreateActionHandler
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentMemory
from production_backend.app.workers.errors import PermanentJobError


def test_agent_memory_create_action_handler_creates_memory_through_service() -> None:
    service = FakeMemoryService()
    action = _action(
        apply_payload={
            "memory_type": "communication_preference",
            "content": {"summary": "Prefers concise reminders"},
            "confidence_score": 80,
        }
    )

    result = asyncio.run(AgentMemoryCreateActionHandler(service=service)(action))

    assert result.resource_type == "agent_memory"
    assert result.resource_id == str(service.memory.id)
    assert result.details == {
        "memory_type": "communication_preference",
        "agent_action_id": str(action.id),
        "agent_run_id": str(action.run_id),
    }
    assert service.create_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_kwargs["source_run_id"] == action.run_id
    assert service.create_kwargs["content"] == {"summary": "Prefers concise reminders"}
    assert service.create_kwargs["confidence_score"] == 80


def test_agent_memory_create_action_handler_rejects_missing_payload() -> None:
    with pytest.raises(PermanentJobError) as type_exc:
        asyncio.run(AgentMemoryCreateActionHandler(service=FakeMemoryService())(_action(apply_payload={"content": {"summary": "x"}})))
    with pytest.raises(PermanentJobError) as content_exc:
        asyncio.run(
            AgentMemoryCreateActionHandler(service=FakeMemoryService())(
                _action(apply_payload={"memory_type": "user_preference"})
            )
        )

    assert type_exc.value.code == "missing_memory_type"
    assert content_exc.value.code == "missing_memory_content"


class FakeMemoryService:
    def __init__(self) -> None:
        self.memory = AgentMemory(
            id=uuid4(),
            owner_user_id=uuid4(),
            memory_type="communication_preference",
            content={"summary": "Prefers concise reminders"},
            status="active",
        )
        self.create_kwargs = {}

    async def create_memory(self, **kwargs):
        self.create_kwargs = kwargs
        self.memory.owner_user_id = kwargs["owner_user_id"]
        self.memory.memory_type = kwargs["memory_type"]
        self.memory.content = kwargs["content"]
        self.memory.source_run_id = kwargs["source_run_id"]
        self.memory.confidence_score = kwargs["confidence_score"]
        return self.memory


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=AGENT_MEMORY_CREATE_ACTION,
        target_type="agent_memory",
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
