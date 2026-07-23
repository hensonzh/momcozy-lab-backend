import asyncio
from uuid import uuid4

from app.agent_runtime.evals.service import AgentEvalService
from app.agent_runtime.runs.models import AgentEvalCase


def test_agent_eval_service_creates_draft_case_from_replay_bundle() -> None:
    run_id = uuid4()
    repository = FakeEvalRepository()
    replay_service = FakeReplayService(run_id=run_id)

    eval_case = asyncio.run(
        AgentEvalService(repository=repository, replay_service=replay_service).create_case_from_run(
            run_id=run_id,
            suite="regression",
            name="support ticket proposal",
            domain="support",
            owner_team="backend",
        )
    )

    assert eval_case.status == "draft"
    assert eval_case.source_run_id == run_id
    assert eval_case.input_payload["replay_bundle"]["messages"][0]["content"] == {"redacted": True}
    assert eval_case.expected_behavior["event_types"] == ["run.started", "action.confirmation_required"]
    assert eval_case.expected_tool_calls == [{"tool_name": "profile_read", "status": "completed"}]


class FakeEvalRepository:
    async def create_eval_case(self, **kwargs):
        return AgentEvalCase(
            id=uuid4(),
            suite=kwargs["suite"],
            name=kwargs["name"],
            domain=kwargs["domain"],
            input_payload=kwargs["input_payload"],
            expected_behavior=kwargs["expected_behavior"],
            expected_tool_calls=kwargs["expected_tool_calls"],
            source_run_id=kwargs["source_run_id"],
            status=kwargs["status"],
            owner_team=kwargs["owner_team"],
        )


class FakeReplayService:
    def __init__(self, *, run_id) -> None:
        self.run_id = run_id

    async def export_run_bundle(self, *, run_id, include_message_content=False):
        assert run_id == self.run_id
        assert include_message_content is False
        return {
            "run": {"id": str(run_id), "status": "waiting_for_confirmation"},
            "messages": [{"content": {"redacted": True}}],
            "events": [{"type": "run.started"}, {"type": "action.confirmation_required"}],
            "tool_calls": [{"tool_name": "profile_read", "status": "completed"}],
            "actions": [{"status": "confirmation_required"}],
            "artifacts": [],
            "checkpoints": [],
            "workflow_states": [],
            "context_items": [],
        }
