import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentMessage, AgentRun, AgentSafetyEvent, AgentToolCall
from production_backend.app.modules.agent_runtime.replay import AgentReplayService


def test_agent_replay_service_exports_redacted_bundle_by_default() -> None:
    repository = FakeReplayRepository()

    bundle = asyncio.run(AgentReplayService(repository=repository).export_run_bundle(run_id=repository.run.id))

    assert bundle["run"]["id"] == str(repository.run.id)
    assert bundle["messages"][0]["content"] == {"redacted": True}
    assert bundle["events"][0]["type"] == "run.started"
    assert bundle["tool_calls"][0]["safe_args"] == {"limit": 1}
    assert bundle["actions"][0]["status"] == "confirmation_required"
    assert bundle["safety_events"][0]["decision"] == "allow"


def test_agent_replay_service_can_include_message_content_when_explicitly_requested() -> None:
    repository = FakeReplayRepository()

    bundle = asyncio.run(
        AgentReplayService(repository=repository).export_run_bundle(run_id=repository.run.id, include_message_content=True)
    )

    assert bundle["messages"][0]["content"] == {"text": "hello"}


class FakeReplayRepository:
    def __init__(self) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="completed",
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
            prompt_version="prompt-v1",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.message = AgentMessage(
            id=uuid4(),
            thread_id=self.run.thread_id,
            run_id=self.run.id,
            role="user",
            message_type="text",
            content={"text": "hello"},
            status="completed",
            sequence=1,
        )
        self.event = AgentEvent(
            event_id=uuid4(),
            thread_id=self.run.thread_id,
            run_id=self.run.id,
            sequence=1,
            event_type="run.started",
            payload={},
        )
        self.tool_call = AgentToolCall(
            id=uuid4(),
            run_id=self.run.id,
            tool_name="profile.read",
            call_id="call_1",
            status="completed",
            safe_args={"limit": 1},
            error_code="",
        )
        self.action = AgentAction(
            id=uuid4(),
            run_id=self.run.id,
            actor_user_id=self.run.actor_user_id,
            action_type="support.ticket.create",
            target_type="support_ticket",
            target_id="",
            status="confirmation_required",
            side_effect_level="medium",
            preview_payload={"summary": "Pump does not turn on"},
            apply_payload={},
            idempotency_key="idem-action",
            error_code="",
        )
        self.safety_event = AgentSafetyEvent(
            id=uuid4(),
            run_id=self.run.id,
            owner_user_id=self.run.actor_user_id,
            category="none",
            severity="low",
            decision="allow",
            evidence={},
            evidence_ref="",
        )

    async def get_run(self, *, run_id):
        return self.run if run_id == self.run.id else None

    async def list_messages_for_thread(self, *, thread_id):
        return [self.message] if thread_id == self.run.thread_id else []

    async def list_events_for_run(self, *, run_id):
        return [self.event] if run_id == self.run.id else []

    async def list_tool_calls_for_run(self, *, run_id):
        return [self.tool_call] if run_id == self.run.id else []

    async def list_actions_for_run(self, *, run_id):
        return [self.action] if run_id == self.run.id else []

    async def list_safety_events_for_run(self, *, run_id):
        return [self.safety_event] if run_id == self.run.id else []
