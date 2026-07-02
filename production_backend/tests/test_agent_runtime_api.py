from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.agent_runtime.models import AgentEvent, AgentRun, AgentThread
from production_backend.app.modules.agent_runtime.router import get_agent_runtime_service
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
    assert fake_service.get_action_kwargs["owner_user_id"] == user_id
    assert fake_service.confirm_action_kwargs["idempotency_key"] == "idem-action"
    assert fake_service.reject_action_kwargs["reason"] == "not now"


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
    def __init__(self, *, user_id: UUID, action_id: UUID | None = None) -> None:
        self.user_id = user_id
        self.thread_id = uuid4()
        self.run_id = uuid4()
        self.action_id = action_id or uuid4()
        self.create_run_kwargs = {}
        self.list_threads_kwargs = {}
        self.get_run_kwargs = {}
        self.list_events_kwargs = {}
        self.cancel_kwargs = {}
        self.get_action_kwargs = {}
        self.confirm_action_kwargs = {}
        self.reject_action_kwargs = {}

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
        return [
            AgentEvent(
                event_id=uuid4(),
                thread_id=self.thread_id,
                run_id=self.run_id,
                sequence=2,
                event_type="run.queued",
                payload={},
            )
        ]

    async def cancel_run(self, **kwargs):
        self.cancel_kwargs = kwargs
        run = self._run()
        run.status = "cancelled"
        return run

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
