import asyncio
import json
from datetime import date
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.actions.outbox import (
    AgentActionApplyResult,
    AgentActionOutboxHandler,
    AgentApplicationEvent,
)
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentRun
from production_backend.app.modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB
from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.modules.diary.agent_actions import (
    PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
    PregnancyDiaryEntryDeleteActionHandler,
)
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.plans.agent_actions import (
    PREGNANCY_PLAN_CREATE_ACTION,
    PregnancyPlanCreateActionHandler,
)
from production_backend.app.workers.errors import PermanentJobError, RetryableJobError


PRIVATE_DELETED_DIARY_CONTENT = "private deleted diary narrative"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"


def test_agent_action_outbox_handler_applies_action_and_emits_event() -> None:
    repository = FakeAgentActionRepository()
    calls = []

    async def apply(action: AgentAction) -> AgentActionApplyResult:
        calls.append(action.id)
        return AgentActionApplyResult(resource_type="support_ticket", resource_id="ticket_1", details={"status": "submitted"})

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    asyncio.run(handler(_job(repository.action.id)))

    assert calls == [repository.action.id]
    assert repository.action.status == "applied"
    assert [event.event_type for event in repository.events] == ["action.applied"]
    assert repository.events[0].thread_id == repository.run.thread_id
    assert repository.events[0].payload["action_id"] == str(repository.action.id)
    assert repository.events[0].payload["action_status"] == "applied"
    assert repository.events[0].payload["action_type"] == "support.ticket.create"
    assert repository.events[0].payload["target_type"] == "support_ticket"
    assert repository.events[0].payload["resource_id"] == "ticket_1"


def test_agent_action_outbox_handler_completes_waiting_run_after_apply_event() -> None:
    repository = FakeAgentActionRepository(run_status="waiting_for_confirmation")

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        return AgentActionApplyResult(resource_type="support_ticket", resource_id="ticket_1")

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    asyncio.run(handler(_job(repository.action.id)))

    assert repository.run.status == "completed"
    assert [event.event_type for event in repository.events] == ["action.applied", "run.completed"]
    assert repository.events[-1].payload == {"reason": "action_applied", "action_id": str(repository.action.id)}


def test_pregnancy_diary_committed_delete_emits_durable_changed_event_without_content() -> None:
    repository = FakeAgentActionRepository(
        action_type=PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
        target_type="pregnancy_diary_entry",
        target_id="2026-07-04",
        apply_payload={"entry_date": "2026-07-04"},
    )
    handler = AgentActionOutboxHandler(
        repository=repository,
        handlers={
            PREGNANCY_DIARY_ENTRY_DELETE_ACTION: PregnancyDiaryEntryDeleteActionHandler(
                service=FakeDiaryDeleteService(owner_user_id=repository.action.actor_user_id)
            )
        },
    )

    asyncio.run(handler(_job(repository.action.id)))

    assert [event.event_type for event in repository.events] == [
        "action.applied",
        "pregnancy_diary.changed",
    ]
    changed_payload_json = json.dumps(repository.events[-1].payload, ensure_ascii=False)
    assert repository.events[-1].payload["operation"] == "deleted"
    assert repository.events[-1].payload["entry_date"] == "2026-07-04"
    assert repository.events[-1].payload["source"] == "agent_action"
    assert repository.events[-1].payload["action_id"] == str(repository.action.id)
    assert '"content"' not in changed_payload_json
    assert PRIVATE_DELETED_DIARY_CONTENT not in changed_payload_json


def test_pregnancy_diary_delete_batches_action_change_and_run_completion() -> None:
    repository = FakeAgentActionRepository(
        run_status="waiting_for_confirmation",
        action_type=PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
        target_type="pregnancy_diary_entry",
        target_id="2026-07-04",
        apply_payload={"entry_date": "2026-07-04"},
    )
    event_sink = CapturingActionEventSink(repository)
    handler = AgentActionOutboxHandler(
        repository=repository,
        event_sink=event_sink,  # type: ignore[arg-type]
        handlers={
            PREGNANCY_DIARY_ENTRY_DELETE_ACTION: PregnancyDiaryEntryDeleteActionHandler(
                service=FakeDiaryDeleteService(owner_user_id=repository.action.actor_user_id)
            )
        },
    )

    asyncio.run(handler(_job(repository.action.id)))

    assert len(event_sink.batches) == 1
    assert [event_type for event_type, _payload in event_sink.batches[0]] == [
        "action.applied",
        "pregnancy_diary.changed",
        "run.completed",
    ]
    assert event_sink.cleared_active_run == (repository.run.thread_id, repository.run.id)


def test_pregnancy_diary_failed_delete_does_not_emit_changed_event() -> None:
    repository = FakeAgentActionRepository(
        action_type=PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
        target_type="pregnancy_diary_entry",
        target_id="2026-07-04",
        apply_payload={"entry_date": "2026-07-04"},
    )
    handler = AgentActionOutboxHandler(
        repository=repository,
        handlers={
            PREGNANCY_DIARY_ENTRY_DELETE_ACTION: PregnancyDiaryEntryDeleteActionHandler(
                service=FakeDiaryDeleteService(owner_user_id=repository.action.actor_user_id, mode="not_found")
            )
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id)))

    assert exc_info.value.code == "not_found"
    assert repository.action.status == "failed"
    assert "pregnancy_diary.changed" not in [event.event_type for event in repository.events]


def test_pregnancy_diary_applied_delete_replay_does_not_emit_duplicate_changed_event() -> None:
    repository = FakeAgentActionRepository(
        action_status="applied",
        action_type=PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
        target_type="pregnancy_diary_entry",
        target_id="2026-07-04",
        apply_payload={"entry_date": "2026-07-04"},
    )
    handler = AgentActionOutboxHandler(repository=repository, handlers={})

    asyncio.run(handler(_job(repository.action.id)))

    assert repository.events == []


def test_failed_pregnancy_plan_create_does_not_emit_changed_event() -> None:
    repository = FakeAgentActionRepository(
        action_type=PREGNANCY_PLAN_CREATE_ACTION,
        target_type="plan",
        apply_payload={"title": "Pregnancy plan"},
    )
    handler = AgentActionOutboxHandler(
        repository=repository,
        handlers={
            PREGNANCY_PLAN_CREATE_ACTION: PregnancyPlanCreateActionHandler(service=FailingPlansService())
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id)))

    assert exc_info.value.code == "plan_create_failed"
    assert repository.action.status == "failed"
    assert [event.event_type for event in repository.events] == ["action.failed"]
    assert PREGNANCY_PLAN_CHANGED_EVENT not in [event.event_type for event in repository.events]


def test_action_apply_event_failure_rolls_back_mutation_scope_before_retry() -> None:
    repository = FaultInjectingAgentActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        repository.mutations.append("diary_deleted")
        return AgentActionApplyResult(
            resource_type="pregnancy_diary_entry",
            resource_id="diary-1",
            application_events=(
                AgentApplicationEvent(
                    event_type="pregnancy_diary.changed",
                    payload={"operation": "deleted"},
                ),
            ),
        )

    handler = AgentActionOutboxHandler(repository=repository, handlers={repository.action.action_type: apply})

    with pytest.raises(RetryableJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id, attempts=1, max_attempts=3)))

    assert exc_info.value.code == "agent_action_handler_error"
    assert repository.mutations == []
    assert repository.events == []
    assert repository.action.status == "applying"


def test_final_action_apply_failure_reloads_waiting_run_before_failure_completion() -> None:
    repository = FaultInjectingAgentActionRepository(run_status="waiting_for_confirmation")

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        repository.mutations.append("diary_deleted")
        return AgentActionApplyResult(
            resource_type="pregnancy_diary_entry",
            resource_id="diary-1",
            application_events=(
                AgentApplicationEvent(
                    event_type="pregnancy_diary.changed",
                    payload={"operation": "deleted"},
                ),
            ),
        )

    handler = AgentActionOutboxHandler(repository=repository, handlers={repository.action.action_type: apply})

    with pytest.raises(RetryableJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id, attempts=3, max_attempts=3)))

    assert exc_info.value.code == "agent_action_handler_error"
    assert repository.mutations == []
    assert repository.action.status == "failed"
    assert repository.run.status == "completed"
    assert [event.event_type for event in repository.events] == ["action.failed", "run.completed"]
    assert repository.get_action_count >= 2
    assert repository.get_run_count >= 2


def test_agent_action_outbox_handler_marks_failed_when_handler_missing() -> None:
    repository = FakeAgentActionRepository()
    handler = AgentActionOutboxHandler(repository=repository, handlers={})

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id)))

    assert exc_info.value.code == "agent_action_handler_not_found"
    assert repository.action.status == "failed"
    assert repository.action.error_code == "agent_action_handler_not_found"
    assert repository.events[-1].event_type == "action.failed"
    assert repository.events[-1].payload["action_id"] == str(repository.action.id)
    assert repository.events[-1].payload["action_status"] == "failed"
    assert repository.events[-1].payload["action_type"] == "support.ticket.create"
    assert repository.events[-1].payload["target_type"] == "support_ticket"
    assert repository.events[-1].payload["code"] == "agent_action_handler_not_found"


def test_agent_action_outbox_handler_is_idempotent_for_applied_action() -> None:
    repository = FakeAgentActionRepository(action_status="applied")
    handler = AgentActionOutboxHandler(repository=repository, handlers={})

    asyncio.run(handler(_job(repository.action.id)))

    assert repository.events == []


def test_agent_action_outbox_handler_keeps_retryable_action_applying_before_final_attempt() -> None:
    repository = FakeAgentActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        raise RetryableJobError("provider_503")

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    with pytest.raises(RetryableJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id, attempts=1, max_attempts=3)))

    assert exc_info.value.code == "provider_503"
    assert repository.action.status == "applying"
    assert repository.action.error_code == ""
    assert repository.events == []


def test_agent_action_outbox_handler_fails_retryable_action_on_final_attempt() -> None:
    repository = FakeAgentActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        raise RetryableJobError("provider_503")

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    with pytest.raises(RetryableJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id, attempts=3, max_attempts=3)))

    assert exc_info.value.code == "provider_503"
    assert repository.action.status == "failed"
    assert repository.action.error_code == "provider_503"
    assert repository.events[-1].event_type == "action.failed"
    assert repository.events[-1].payload["action_status"] == "failed"
    assert repository.events[-1].payload["code"] == "provider_503"


def test_agent_action_outbox_handler_fails_unexpected_error_on_final_attempt() -> None:
    repository = FakeAgentActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        raise RuntimeError("provider exploded")

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    with pytest.raises(RetryableJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id, attempts=3, max_attempts=3)))

    assert exc_info.value.code == "agent_action_handler_error"
    assert repository.action.status == "failed"
    assert repository.action.error_code == "agent_action_handler_error"
    assert repository.events[-1].event_type == "action.failed"


class FakeDiaryDeleteService:
    def __init__(self, *, owner_user_id, mode: str = "success") -> None:
        self.owner_user_id = owner_user_id
        self.mode = mode
        self.entry = PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=owner_user_id,
            entry_date=date(2026, 7, 4),
            mood="calm",
            content=PRIVATE_DELETED_DIARY_CONTENT,
            symptom_tags=[],
            attachments=[],
        )

    async def delete_entry(self, **kwargs):
        assert kwargs["owner_user_id"] == self.owner_user_id
        if self.mode == "not_found":
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        return self.entry


class FailingPlansService:
    async def create_plan(self, **_kwargs):
        raise ApiError(code="plan_create_failed", message="Plan could not be created.", status=500)


class FakeAgentActionRepository:
    def __init__(
        self,
        *,
        action_status: str = "confirmed",
        run_status: str = "completed",
        action_type: str = "support.ticket.create",
        target_type: str = "support_ticket",
        target_id: str = "",
        apply_payload: dict | None = None,
    ) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status=run_status,
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
            prompt_version="",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.action = AgentAction(
            id=uuid4(),
            run_id=self.run.id,
            actor_user_id=self.run.actor_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            status=action_status,
            side_effect_level="medium",
            preview_payload={},
            apply_payload=apply_payload or {},
            idempotency_key="idem-action",
            error_code="",
        )
        self.events = []
        self.get_action_count = 0
        self.get_run_count = 0

    async def get_action(self, *, action_id):
        self.get_action_count += 1
        return self.action if action_id == self.action.id else None

    async def get_run(self, *, run_id):
        self.get_run_count += 1
        return self.run if run_id == self.run.id else None

    async def mark_action_applying(self, *, action):
        action.status = "applying"
        return action

    async def mark_action_applied(self, *, action, applied_at):
        action.status = "applied"
        action.applied_at = applied_at
        return action

    async def mark_action_failed(self, *, action, failed_at, error_code):
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        return action

    async def mark_run_completed(self, *, run, completed_at):
        run.status = "completed"
        run.completed_at = completed_at
        return run

    async def append_event(self, **kwargs):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event


class CapturingActionEventSink:
    def __init__(self, repository: FakeAgentActionRepository) -> None:
        self.repository = repository
        self.batches = []
        self.cleared_active_run = None

    async def append_events(self, *, thread_id, run_id, events):
        self.batches.append(events)
        for event_type, payload in events:
            await self.repository.append_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
            )

    async def clear_active_run(self, *, thread_id, run_id):
        self.cleared_active_run = (thread_id, run_id)


class FaultInjectingAgentActionRepository(FakeAgentActionRepository):
    def __init__(self, *, run_status: str = "completed") -> None:
        super().__init__(run_status=run_status)
        self.mutations: list[str] = []
        self.apply_session = FakeApplySession(self)

    def begin_nested(self):
        return self.apply_session.begin_nested()

    async def append_event(self, **kwargs):
        if kwargs["event_type"] == "pregnancy_diary.changed":
            raise RuntimeError("event append failed")
        return await super().append_event(**kwargs)


class FakeApplySession:
    def __init__(self, repository: FaultInjectingAgentActionRepository) -> None:
        self.repository = repository

    def begin_nested(self):
        return FakeApplySavepoint(self.repository)


class FakeApplySavepoint:
    def __init__(self, repository: FaultInjectingAgentActionRepository) -> None:
        self.repository = repository
        self.action_status = repository.action.status
        self.run_status = repository.run.status
        self.events = list(repository.events)
        self.mutations = list(repository.mutations)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.repository.action.status = self.action_status
            self.repository.run.status = self.run_status
            self.repository.events[:] = self.events
            self.repository.mutations[:] = self.mutations
        return False


def _job(action_id, *, attempts: int = 0, max_attempts: int = 3) -> OutboxJob:
    return OutboxJob(
        id=uuid4(),
        action_id=action_id,
        job_type=AGENT_ACTION_APPLY_JOB,
        status="locked",
        payload={"action_id": str(action_id)},
        idempotency_key=f"agent-action:{action_id}",
        attempts=attempts,
        max_attempts=max_attempts,
        request_id="req",
        trace_id="trace",
    )
