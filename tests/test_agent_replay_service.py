import asyncio
from uuid import uuid4

from app.agents.cozymate.replay import project_cozymate_workflow_replay_state
from app.agent_runtime.runs.models import (
    AgentAction,
    AgentArtifact,
    AgentContextItem,
    AgentEvent,
    AgentMessage,
    AgentModelContextSnapshot,
    AgentRun,
    AgentToolCall,
    AgentWorkflowEvent,
    AgentWorkflowState,
)
from app.agent_runtime.events.replay import AgentReplayService


def test_agent_replay_service_exports_redacted_bundle_by_default() -> None:
    repository = FakeReplayRepository()

    bundle = asyncio.run(AgentReplayService(repository=repository).export_run_bundle(run_id=repository.run.id))

    assert bundle["run"]["id"] == str(repository.run.id)
    assert bundle["run"]["runtime_pattern"] == "sdk_only"
    assert bundle["run"]["runtime_version"] == "momcozy-agent-v2"
    assert "graph_version" not in bundle["run"]
    assert "prompt_version" not in bundle["run"]
    assert bundle["messages"][0]["content"] == {"redacted": True}
    assert [item["item_type"] for item in bundle["context_items"]] == [
        "message",
        "function_call_output",
    ]
    assert bundle["context_items"][0]["item"] == {"redacted": True}
    assert bundle["events"][0]["type"] == "run.started"
    assert bundle["events"][0]["thread_id"] == str(repository.run.thread_id)
    assert bundle["events"][0]["run_id"] == str(repository.run.id)
    assert bundle["tool_calls"][0]["safe_args"] == {"limit": 1}
    assert bundle["actions"][0]["status"] == "confirmation_required"
    assert bundle["artifacts"][0]["artifact_type"] == "care_plan"
    assert bundle["artifacts"][0]["payload"] == {"title": "Birth plan"}
    assert bundle["checkpoints"] == []
    assert bundle["workflow_states"][0]["workflow_type"] == "milk_analysis_intake"
    assert bundle["workflow_events"][0]["event_type"] == "workflow.answer_recorded"
    assert bundle["workflow_events"][0]["payload"]["interaction"]["answer"] == "[redacted]"
    assert bundle["workflow_events"][0]["payload"]["after"]["state"] == {"redacted": True}
    assert bundle["model_context_snapshots"][0]["dynamic_context"] == {
        "redacted": True
    }
    assert bundle["model_context_snapshots"][0]["model_input_sha256"] == "a" * 64
    assert "context_projections" not in bundle


def test_agent_replay_service_can_include_message_content_when_explicitly_requested() -> None:
    repository = FakeReplayRepository()

    bundle = asyncio.run(
        AgentReplayService(repository=repository).export_run_bundle(run_id=repository.run.id, include_message_content=True)
    )

    assert bundle["messages"][0]["content"] == {"text": "hello"}
    assert bundle["context_items"][0]["item"] == {"role": "user", "content": "hello"}


def test_agent_replay_service_redacts_pii_from_export_payloads() -> None:
    repository = FakeReplayRepository()
    repository.run.error_details = {"contact_email": "parent@example.com"}
    repository.message.content = {"text": "Email me at parent@example.com"}
    repository.event.payload = {"phone": "+1 415 555 1212", "visible": "ok"}
    repository.tool_call.safe_args = {"api_token": "secret-token", "limit": 1}
    repository.action.preview_payload = {"user_contact": "parent@example.com", "summary": "Pump issue"}
    repository.artifact.payload = {"shipping_address": "1 Main Street", "title": "Birth plan"}
    repository.workflow_state.state = {"phone_number": "4155551212", "step": "collect"}
    repository.context_items[1].item["output"] = '{"email":"parent@example.com"}'

    bundle = asyncio.run(
        AgentReplayService(repository=repository).export_run_bundle(run_id=repository.run.id, include_message_content=True)
    )

    assert bundle["run"]["error_details"] == {"contact_email": "[redacted]"}
    assert bundle["messages"][0]["content"] == {"text": "[redacted]"}
    assert bundle["events"][0]["payload"] == {"phone": "[redacted]", "visible": "ok"}
    assert bundle["tool_calls"][0]["safe_args"] == {"api_token": "[redacted]", "limit": 1}
    assert bundle["actions"][0]["preview_payload"] == {"user_contact": "[redacted]", "summary": "Pump issue"}
    assert bundle["artifacts"][0]["payload"] == {"shipping_address": "[redacted]", "title": "Birth plan"}
    assert bundle["workflow_states"][0]["state"] == {"phone_number": "[redacted]", "step": "collect"}
    assert bundle["context_items"][1]["item"]["output"] == "[redacted]"


def test_agent_replay_service_projects_pregnancy_workflow_state_without_health_facts() -> None:
    repository = FakeReplayRepository()
    repository.workflow_state.workflow_type = "pregnancy_plan"
    repository.workflow_state.state = {
        "phase": "awaiting_additional_information",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "plan_context": {
            "age": 36,
            "ivf": "是",
            "fetus_count": "双胎",
            "medical_notes": "甲状腺用药",
            "doctor_notes": "复查胎儿生长",
        },
        "analysis": {
            "focuses": [
                {"id": "advanced_maternal_age", "management_meaning": "private"},
                {"id": "medical_coordination", "management_meaning": "private"},
            ]
        },
    }

    bundle = asyncio.run(
        AgentReplayService(
            repository=repository,
            workflow_state_projector=project_cozymate_workflow_replay_state,
        ).export_run_bundle(run_id=repository.run.id)
    )

    assert bundle["workflow_states"][0]["state"] == {
        "phase": "awaiting_additional_information",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "focus_count": 2,
        "personalized": True,
    }
    assert "甲状腺" not in str(bundle)


class FakeReplayRepository:
    def __init__(self) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="completed",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
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
            tool_name="maternal_infant_profile_read",
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
        self.artifact = AgentArtifact(
            id=uuid4(),
            run_id=self.run.id,
            owner_user_id=self.run.actor_user_id,
            artifact_type="care_plan",
            schema_version="v1",
            status="created",
            payload={"title": "Birth plan"},
            raw_payload_ref="",
        )
        self.workflow_state = AgentWorkflowState(
            id=uuid4(),
            thread_id=self.run.thread_id,
            owner_user_id=self.run.actor_user_id,
            run_id=self.run.id,
            workflow_type="milk_analysis_intake",
            status="collecting",
            schema_version="v1",
            state={"current_field": "daily_volume"},
            active_step="collect_daily_volume",
        )
        self.workflow_event = AgentWorkflowEvent(
            id=uuid4(),
            workflow_state_id=self.workflow_state.id,
            owner_user_id=self.run.actor_user_id,
            thread_id=self.run.thread_id,
            run_id=self.run.id,
            workflow_type=self.workflow_state.workflow_type,
            sequence=1,
            event_type="workflow.answer_recorded",
            from_revision=1,
            to_revision=2,
            payload={
                "command": "answer_current",
                "interaction": {"answer": "private answer"},
                "after": {
                    "status": "waiting",
                    "active_step": "next",
                    "revision": 2,
                    "state": {"answer": "private answer"},
                },
            },
        )
        self.model_context_snapshot = AgentModelContextSnapshot(
            id=uuid4(),
            run_id=self.run.id,
            thread_id=self.run.thread_id,
            owner_user_id=self.run.actor_user_id,
            sequence=1,
            schema_version="model_context_snapshot.v1",
            item_refs=[
                {
                    "item_key": f"message:{self.message.id}",
                    "sequence": 1,
                    "model_input_position": 0,
                }
            ],
            dynamic_context={"runtime_context": {"private": "health details"}},
            selection_policy={"model_context_token_budget": 12000},
            input_item_count=1,
            estimated_input_tokens=12,
            model_input_sha256="a" * 64,
        )
        self.context_items = [
            AgentContextItem(
                id=uuid4(),
                thread_id=self.run.thread_id,
                run_id=self.run.id,
                item_key=f"message:{self.message.id}",
                item_type="message",
                item={"role": "user", "content": "hello"},
                sequence=1,
            ),
            AgentContextItem(
                id=uuid4(),
                thread_id=self.run.thread_id,
                run_id=self.run.id,
                item_key="run:1:function_call_output:call_1",
                item_type="function_call_output",
                item={
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"profile":{"preferred_name":"Mai"}}',
                },
                sequence=2,
            ),
        ]

    async def get_run(self, *, run_id):
        return self.run if run_id == self.run.id else None

    async def list_messages_for_thread(self, *, thread_id):
        return [self.message] if thread_id == self.run.thread_id else []

    async def list_context_items_for_thread(self, *, thread_id):
        return list(self.context_items) if thread_id == self.run.thread_id else []

    async def list_events_for_run(self, *, run_id):
        return [self.event] if run_id == self.run.id else []

    async def list_tool_calls_for_run(self, *, run_id):
        return [self.tool_call] if run_id == self.run.id else []

    async def list_actions_for_run(self, *, run_id):
        return [self.action] if run_id == self.run.id else []

    async def list_artifacts_for_run(self, *, run_id):
        return [self.artifact] if run_id == self.run.id else []

    async def list_workflow_states_for_run(self, *, run_id):
        return [self.workflow_state] if run_id == self.run.id else []

    async def list_workflow_events_for_run(self, *, run_id):
        return [self.workflow_event] if run_id == self.run.id else []

    async def list_model_context_snapshots_for_run(self, *, run_id):
        return [self.model_context_snapshot] if run_id == self.run.id else []
