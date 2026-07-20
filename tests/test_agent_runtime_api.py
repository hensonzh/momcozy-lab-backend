import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.agent_runtime.models import (
    AgentEvalCase,
    AgentEvent,
    AgentMemory,
    AgentMemorySettings,
    AgentRun,
    AgentThread,
)
from app.modules.agent_runtime.router import (
    _stream_run_event_chunks,
    get_agent_eval_service,
    get_agent_fact_service,
    get_agent_memory_service,
    get_agent_replay_service,
    get_agent_runtime_service,
)
from app.modules.agent_runtime.facts.models import UserFact
from app.modules.auth import CurrentUser


def test_agent_runtime_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post("/v1/agent/runs", json={"message": "Hello"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_runtime_dependencies_share_consent_aware_configured_fact_service() -> None:
    settings = Settings(
        app_env="test",
        agent_fact_extraction_enabled=False,
        agent_fact_extraction_model="fact-model",
        agent_fact_extraction_version="fact-v9",
        agent_fact_worker_max_attempts=7,
    )
    app = create_app(settings)
    app.state.redis_client = None
    request = Request({"type": "http", "app": app})

    service = get_agent_runtime_service(request=request, session=object())  # type: ignore[arg-type]

    assert service.memory_service is not None
    assert service.fact_service is not None
    assert service.fact_service.memory_consent_reader is service.memory_service
    assert service.fact_service.audit_service is not None
    assert service.fact_service.extraction_enabled is False
    assert service.fact_service.extraction_model == "fact-model"
    assert service.fact_service.extraction_version == "fact-v9"
    assert service.fact_service.extraction_max_attempts == 7


def test_create_run_uses_current_user_request_id_and_idempotency_key(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.agent_runtime")
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/agent/runs",
        headers={"X-Request-ID": "req_agent", "Idempotency-Key": " idem-run "},
        json={
            "message": "Review my pumping pattern",
            "client_context": {
                "source": "flutter-agent-hub",
                "locale": "en-US",
                "hospital_bag_cart": {
                    "groups": [
                        {
                            "title": "Feeding",
                            "tone": "sky",
                            "items": [{"id": "pump-custom", "name": "Custom pump", "qty": 1, "price": 999.0}],
                        }
                    ],
                    "totals": {"itemCount": 1, "total": 919.08},
                },
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["runtime_pattern"] == "sdk_only"
    assert response.json()["runtime_version"] == "momcozy-agent-v1"
    assert "graph_version" not in response.json()
    assert "prompt_version" not in response.json()
    assert fake_service.create_run_kwargs["actor_user_id"] == user_id
    assert fake_service.create_run_kwargs["request_id"] == "req_agent"
    assert "prompt_version" not in fake_service.create_run_kwargs
    assert fake_service.create_run_kwargs["idempotency_key"] == "idem-run"
    assert fake_service.create_run_kwargs["client_context"]["hospital_bag_cart"]["groups"][0]["items"][0]["id"] == "pump-custom"
    payloads = [json.loads(record.getMessage()) for record in caplog.records if record.name == "production_backend.agent_runtime"]
    timing = next(payload for payload in payloads if payload["event"] == "agent.run.api_create")
    assert timing["request_id"] == "req_agent"
    assert timing["run_id"] == str(fake_service.run_id)
    assert timing["thread_id"] == str(fake_service.thread_id)
    assert timing["status"] == "queued"
    assert timing["duration_ms"] >= 0


def test_create_run_rejects_retired_prompt_version_field() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/agent/runs",
        json={"message": "Hello", "prompt_version": "unregistered-prompt"},
    )

    assert response.status_code == 422
    assert fake_service.create_run_kwargs == {}


def test_create_run_rejects_retired_runtime_contract() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/agent/runs",
        json={
            "message": "Legacy app request",
            "runtime_pattern": "langgraph_sdk",
            "graph_version": "momcozy-agent-v1",
        },
    )

    assert response.status_code == 422
    assert fake_service.create_run_kwargs == {}


def test_create_run_rejects_retired_graph_version_field() -> None:
    user_id = uuid4()
    fake_service = FakeAgentRuntimeService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_runtime_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/agent/runs",
        json={
            "message": "Conflicting runtime versions",
            "runtime_version": "momcozy-agent-v2",
            "graph_version": "momcozy-agent-v1",
        },
    )

    assert response.status_code == 422


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
    assert stream.headers["x-accel-buffering"] == "no"
    assert _sse_payloads(stream.text)[0]["type"] == "run.queued"
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
    payloads = _sse_payloads(response.text)
    assert [payload["type"] for payload in payloads] == ["run.progress", "run.completed"]
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


def test_agent_memory_management_uses_current_user_scope() -> None:
    user_id = uuid4()
    memory_id = uuid4()
    fake_service = FakeMemoryService(user_id=user_id, memory_id=memory_id)
    fake_fact_service = FakeFactService(user_id=user_id, fact_id=uuid4())
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_memory_service] = lambda: fake_service
    app.dependency_overrides[get_agent_fact_service] = lambda: fake_fact_service
    client = TestClient(app)

    list_response = client.get("/v1/agent/memories?memory_type=communication_preference&limit=10")
    settings_response = client.get("/v1/agent/memories/settings")
    update_settings_response = client.put(
        "/v1/agent/memories/settings",
        json={"memory_enabled": False},
        headers={"X-Request-ID": "req-memory-disable"},
    )
    delete_response = client.delete(f"/v1/agent/memories/{memory_id}")

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == str(memory_id)
    assert list_response.json()["items"][0]["content"] == {"summary": "Prefers concise reminders"}
    assert settings_response.status_code == 200
    assert settings_response.json()["owner_user_id"] == str(user_id)
    assert settings_response.json()["memory_enabled"] is True
    assert update_settings_response.status_code == 200
    assert update_settings_response.json()["memory_enabled"] is False
    assert delete_response.status_code == 204
    assert fake_service.list_kwargs == {
        "owner_user_id": user_id,
        "memory_type": "communication_preference",
        "limit": 10,
        "include_when_disabled": True,
    }
    assert fake_service.get_settings_kwargs == {"owner_user_id": user_id}
    assert fake_service.update_settings_kwargs == {"owner_user_id": user_id, "memory_enabled": False}
    assert fake_service.archive_kwargs == {"owner_user_id": user_id, "memory_id": memory_id}
    assert fake_fact_service.cancel_pending_kwargs == {
        "owner_user_id": user_id,
        "reason": "memory_disabled",
        "request_id": "req-memory-disable",
    }


def test_agent_fact_management_is_owner_scoped_and_omits_internal_provenance() -> None:
    user_id = uuid4()
    fact_id = uuid4()
    fake_service = FakeFactService(user_id=user_id, fact_id=fact_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_agent_fact_service] = lambda: fake_service
    client = TestClient(app)

    list_response = client.get("/v1/agent/facts?fact_kind=conversation_candidate&limit=10")
    delete_response = client.delete(f"/v1/agent/facts/{fact_id}", headers={"X-Request-ID": "req-fact-delete"})
    clear_response = client.delete("/v1/agent/facts", headers={"X-Request-ID": "req-facts-clear"})

    assert list_response.status_code == 200
    assert delete_response.status_code == 204
    assert clear_response.status_code == 204
    item = list_response.json()["items"][0]
    assert item["id"] == str(fact_id)
    assert item["value"] == 35
    assert {
        "source_id",
        "source_type",
        "evidence",
        "raw_text",
        "owner_user_id",
        "deletion_reason",
    }.isdisjoint(item)
    assert fake_service.list_kwargs == {
        "owner_user_id": user_id,
        "fact_kind": "conversation_candidate",
        "limit": 10,
        "include_when_disabled": True,
    }
    assert fake_service.delete_kwargs == {
        "owner_user_id": user_id,
        "fact_id": fact_id,
        "request_id": "req-fact-delete",
    }
    assert fake_service.clear_kwargs == {"owner_user_id": user_id, "request_id": "req-facts-clear"}


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
    from app.api.dependencies import require_current_user

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


def _sse_payloads(chunk: str) -> list[dict]:
    payloads = []
    for line in chunk.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


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
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v1",
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
        from app.modules.agent_runtime.models import AgentAction

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


class FakeMemoryService:
    def __init__(self, *, user_id: UUID, memory_id: UUID) -> None:
        self.user_id = user_id
        self.memory_id = memory_id
        self.list_kwargs = {}
        self.archive_kwargs = {}
        self.get_settings_kwargs = {}
        self.update_settings_kwargs = {}

    async def list_active_memories(self, **kwargs):
        self.list_kwargs = kwargs
        return [
            AgentMemory(
                id=self.memory_id,
                owner_user_id=self.user_id,
                memory_type="communication_preference",
                content={"summary": "Prefers concise reminders"},
                schema_version="v1",
                confidence_score=90,
                status="active",
            )
        ]

    async def archive_memory(self, **kwargs):
        self.archive_kwargs = kwargs
        return AgentMemory(
            id=self.memory_id,
            owner_user_id=self.user_id,
            memory_type="communication_preference",
            content={"summary": "Prefers concise reminders"},
            schema_version="v1",
            confidence_score=90,
            status="archived",
        )

    async def get_settings(self, **kwargs):
        self.get_settings_kwargs = kwargs
        return AgentMemorySettings(owner_user_id=kwargs["owner_user_id"], memory_enabled=True)

    async def update_settings(self, **kwargs):
        self.update_settings_kwargs = kwargs
        return AgentMemorySettings(owner_user_id=kwargs["owner_user_id"], memory_enabled=kwargs["memory_enabled"])


class FakeFactService:
    def __init__(self, *, user_id: UUID, fact_id: UUID) -> None:
        self.user_id = user_id
        self.fact_id = fact_id
        self.list_kwargs = {}
        self.delete_kwargs = {}
        self.clear_kwargs = {}
        self.cancel_pending_kwargs = {}

    async def list_facts(self, **kwargs):
        self.list_kwargs = kwargs
        return [
            UserFact(
                id=self.fact_id,
                owner_user_id=self.user_id,
                fact_key="profile.age",
                fact_kind="conversation_candidate",
                status="active",
                value=35,
                source_type="conversation",
                source_id="internal-message-id",
                sensitivity="personal",
                catalog_version="user-fact-catalog-v1",
                observed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            )
        ]

    async def delete_fact(self, **kwargs):
        self.delete_kwargs = kwargs

    async def clear_facts(self, **kwargs):
        self.clear_kwargs = kwargs
        return 1

    async def cancel_pending_extractions(self, **kwargs):
        self.cancel_pending_kwargs = kwargs
        return 2


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
            source_run_id=kwargs["run_id"],
            status="draft",
            owner_team=kwargs["owner_team"],
        )
