import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.agent_runtime.models import AgentEvalCase, AgentEvent, AgentRun, AgentThread
from production_backend.app.modules.agent_runtime.router import (
    _stream_run_event_chunks,
    get_agent_eval_service,
    get_agent_replay_service,
    get_agent_runtime_service,
)
from production_backend.app.modules.auth import CurrentUser


def test_agent_runtime_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post("/v1/agent/runs", json={"message": "Hello"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_run_uses_current_user_request_id_and_idempotency_key() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/agent/runs",
        headers={"X-Request-ID": "req_agent", "Idempotency-Key": " idem-run "},
        json={"message": "Review my pumping pattern"},
    )

    assert response.status_code == 201
    assert response.json()["runtime_pattern"] == "langgraph_sdk"
    assert fake_service.create_run_kwargs["actor_user_id"] == user_id
    assert fake_service.create_run_kwargs["request_id"] == "req_agent"
    assert fake_service.create_run_kwargs["idempotency_key"] == "idem-run"


def test_agent_thread_run_events_and_cancel_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    list_threads = TestClient(app).get("/v1/agent/threads?limit=10")
    get_run = TestClient(app).get(f"/v1/agent/runs/{fake_service.run_id}")
    events = TestClient(app).get(f"/v1/agent/runs/{fake_service.run_id}/events?after_sequence=1&limit=20")
    stream = TestClient(app).get(f"/v1/agent/runs/{fake_service.run_id}/stream?after_sequence=1&limit=20")
    cancel = TestClient(app).post(f"/v1/agent/runs/{fake_service.run_id}/cancel", json={"reason": "stop"})

    assert list_threads.status_code == 200
    assert get_run.status_code == 200
    assert events.status_code == 200
    assert events.json()["items"][0]["type"] == "run.queued"
    assert stream.status_code == 200
    assert "event: run.queued" in stream.text
    assert cancel.status_code == 200
    assert fake_service.list_threads_kwargs["owner_user_id"] == user_id
    assert fake_service.get_run_kwargs["owner_user_id"] == user_id
    assert fake_service.list_events_kwargs["after_sequence"] == 1
    assert fake_service.cancel_kwargs["reason"] == "stop"


def test_agent_client_event_ingest_uses_current_user_and_run_scope() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        f"/v1/agent/runs/{fake_service.run_id}/client-events",
        json={
            "type": "ui.quick_reply.clicked",
            "payload": {"reply_id": "next_step"},
            "client_sequence": 7,
        },
    )

    assert response.status_code == 201
    assert response.json()["type"] == "client.event"
    assert response.json()["payload"]["client_event_type"] == "ui.quick_reply.clicked"
    assert fake_service.record_client_event_kwargs == {
        "owner_user_id": user_id,
        "run_id": fake_service.run_id,
        "client_event_type": "ui.quick_reply.clicked",
        "payload": {"reply_id": "next_step"},
        "client_sequence": 7,
    }


def test_agent_stream_can_follow_until_terminal_event() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(
        user_id=user_id,
        event_batches=[
            [("run.progress", 2)],
            [("run.completed", 3)],
        ],
    )
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).get(
        f"/v1/agent/runs/{fake_service.run_id}/stream?after_sequence=1&follow=true&poll_interval_seconds=0.1&max_wait_seconds=1"
    )

    assert response.status_code == 200
    assert "event: run.progress" in response.text
    assert "event: run.completed" in response.text
    assert fake_service.list_events_call_count == 2


def test_agent_stream_stops_before_polling_when_client_disconnected() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)

    async def disconnected() -> bool:
        return True

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in _stream_run_event_chunks(
                service=fake_service,
                owner_user_id=user_id,
                run_id=fake_service.run_id,
                after_sequence=0,
                limit=20,
                follow=True,
                poll_interval_seconds=0.1,
                max_wait_seconds=1,
                is_disconnected=disconnected,
            )
        ]

    assert asyncio.run(collect()) == []
    assert fake_service.list_events_call_count == 0


def test_agent_action_confirm_and_reject_use_current_user_scope() -> None:
    user_id = uuid4()
    action_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id, action_id=action_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    get_response = TestClient(app).get(f"/v1/agent/actions/{action_id}")
    confirm_response = TestClient(app).post(
        f"/v1/agent/actions/{action_id}/confirm",
        headers={"Idempotency-Key": " idem-action "},
        json={"edited_apply_payload": {"issue_summary": "Updated"}},
    )
    reject_response = TestClient(app).post(f"/v1/agent/actions/{action_id}/reject", json={"reason": "not now"})

    assert get_response.status_code == 200
    assert confirm_response.status_code == 200
    assert reject_response.status_code == 200
    assert "apply_payload" not in get_response.json()
    assert "apply_payload" not in confirm_response.json()
    assert "apply_payload" not in reject_response.json()
    assert "idempotency_key" not in get_response.json()
    assert "idempotency_key" not in confirm_response.json()
    assert "idempotency_key" not in reject_response.json()
    assert fake_service.get_action_kwargs["owner_user_id"] == user_id
    assert fake_service.confirm_action_kwargs["idempotency_key"] == "idem-action"
    assert fake_service.reject_action_kwargs["reason"] == "not now"


def test_agent_artifact_delete_uses_current_user_scope() -> None:
    user_id = uuid4()
    artifact_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).delete(f"/v1/agent/artifacts/{artifact_id}")

    assert response.status_code == 204
    assert fake_service.delete_artifact_kwargs == {"owner_user_id": user_id, "artifact_id": artifact_id}


def test_agent_admin_replay_and_eval_endpoints_require_service_key() -> None:
    run_id = uuid4()
    settings = Settings(app_env="test", service_api_key="test-service-key-with-at-least-32-bytes")
    app = create_app(settings)
    replay_service = FakeReplayService(run_id=run_id)
    eval_service = FakeEvalService(run_id=run_id)
    app.dependency_overrides[get_agent_replay_service] = lambda: replay_service
    app.dependency_overrides[get_agent_eval_service] = lambda: eval_service
    client = TestClient(app)

    unauthorized = client.get(f"/v1/agent/admin/runs/{run_id}/replay")
    replay = client.get(
        f"/v1/agent/admin/runs/{run_id}/replay?include_message_content=true",
        headers={"X-Service-Key": settings.service_api_key},
    )
    eval_response = client.post(
        f"/v1/agent/admin/runs/{run_id}/eval-cases",
        headers={"X-Service-Key": settings.service_api_key},
        json={"suite": "regression", "name": "case 1", "domain": "support", "owner_team": "backend"},
    )

    assert unauthorized.status_code == 401
    assert replay.status_code == 200
    assert replay.json()["run"]["id"] == str(run_id)
    assert replay_service.include_message_content is True
    assert eval_response.status_code == 201
    assert eval_response.json()["suite"] == "regression"
    assert eval_service.create_kwargs["owner_team"] == "backend"


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakeAgentRuntimeService:
    def __init__(
        self,
        *,
        user_id: UUID,
        action_id: UUID | None = None,
        event_batches: list[list[tuple[str, int]]] | None = None,
    ) -> None:
        self.user_id = user_id
        self.thread_id = uuid4()
        self.run_id = uuid4()
        self.action_id = action_id or uuid4()
        self.event_batches = event_batches
        self.list_events_call_count = 0
        self.create_run_kwargs = {}
        self.list_threads_kwargs = {}
        self.get_run_kwargs = {}
        self.list_events_kwargs = {}
        self.cancel_kwargs = {}
        self.get_action_kwargs = {}
        self.confirm_action_kwargs = {}
        self.reject_action_kwargs = {}
        self.record_client_event_kwargs = {}
        self.delete_artifact_kwargs = {}

    async def create_thread(self, **kwargs):
        return self._thread()

    async def list_threads(self, **kwargs):
        self.list_threads_kwargs = kwargs
        return [self._thread()]

    async def get_thread(self, **kwargs):
        return self._thread()

    async def create_run(self, **kwargs):
        self.create_run_kwargs = kwargs
        return self._run()

    async def get_run(self, **kwargs):
        self.get_run_kwargs = kwargs
        return self._run()

    async def list_events(self, **kwargs):
        self.list_events_kwargs = kwargs
        self.list_events_call_count += 1
        if self.event_batches:
            batch = self.event_batches[min(self.list_events_call_count - 1, len(self.event_batches) - 1)]
            return [self._event(event_type=event_type, sequence=sequence) for event_type, sequence in batch]
        return [self._event(event_type="run.queued", sequence=2)]

    async def cancel_run(self, **kwargs):
        self.cancel_kwargs = kwargs
        run = self._run()
        run.status = "cancelled"
        return run

    async def record_client_event(self, **kwargs):
        self.record_client_event_kwargs = kwargs
        event = self._event(event_type="client.event", sequence=3)
        event.payload = {
            "client_event_type": kwargs["client_event_type"],
            "client_sequence": kwargs["client_sequence"],
            "payload": kwargs["payload"],
        }
        return event

    async def get_action(self, **kwargs):
        self.get_action_kwargs = kwargs
        return self._action()

    async def confirm_action(self, **kwargs):
        self.confirm_action_kwargs = kwargs
        action = self._action()
        action.status = "confirmed"
        return action

    async def reject_action(self, **kwargs):
        self.reject_action_kwargs = kwargs
        action = self._action()
        action.status = "rejected"
        return action

    async def delete_artifact(self, **kwargs):
        self.delete_artifact_kwargs = kwargs

    def _thread(self) -> AgentThread:
        return AgentThread(id=self.thread_id, owner_user_id=self.user_id, title="Thread", status="active", metadata_json={})

    def _run(self) -> AgentRun:
        return AgentRun(
            id=self.run_id,
            thread_id=self.thread_id,
            actor_user_id=self.user_id,
            status="queued",
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
            prompt_version="",
            request_id="req_agent",
            trace_id="req_agent",
            error_code="",
            error_details={},
        )

    def _event(self, *, event_type: str, sequence: int) -> AgentEvent:
        return AgentEvent(
            event_id=uuid4(),
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=sequence,
            event_type=event_type,
            payload={},
        )

    def _action(self):
        from production_backend.app.modules.agent_runtime.models import AgentAction

        return AgentAction(
            id=self.action_id,
            run_id=self.run_id,
            actor_user_id=self.user_id,
            action_type="support.ticket.create",
            target_type="support_ticket",
            target_id="",
            status="confirmation_required",
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="",
            error_code="",
        )


class FakeReplayService:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id
        self.include_message_content = False

    async def export_run_bundle(self, *, run_id: UUID, include_message_content: bool = False):
        self.include_message_content = include_message_content
        return {
            "run": {"id": str(run_id), "status": "completed"},
            "messages": [{"content": {"text": "hello"} if include_message_content else {"redacted": True}}],
            "events": [],
            "tool_calls": [],
            "actions": [],
            "artifacts": [],
            "checkpoints": [],
            "workflow_states": [],
            "context_projections": [],
            "safety_events": [],
        }


class FakeEvalService:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id
        self.create_kwargs = {}

    async def create_case_from_run(self, **kwargs):
        self.create_kwargs = kwargs
        return AgentEvalCase(
            id=uuid4(),
            suite=kwargs["suite"],
            name=kwargs["name"],
            domain=kwargs["domain"],
            input_payload={},
            expected_behavior={},
            expected_tool_calls=[],
            expected_safety_decision="",
            source_run_id=kwargs["run_id"],
            status="draft",
            owner_team=kwargs["owner_team"],
        )
