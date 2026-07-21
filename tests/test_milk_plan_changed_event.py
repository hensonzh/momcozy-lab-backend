import asyncio
from datetime import date
from uuid import uuid4

from app.agents.cozymate.actions import cozymate_action_policy
from app.core.errors import ApiError
from app.agent_runtime.actions.executor import AgentActionExecutor
from app.agent_runtime.runs.models import AgentAction, AgentEvent, AgentRun
from app.agents.cozymate.actions.plans import (
    MILK_PLAN_CHANGED_EVENT,
    MILK_PLAN_CREATE_ACTION,
    MILK_SCHEDULE_RESCHEDULE_ACTION,
    MilkPlanCreateActionHandler,
    MilkScheduleRescheduleActionHandler,
)
from app.modules.plans.models import Plan, PlanTask


MILK_ANALYSIS_LINEAGE = {
    "direction": "maintain",
    "analysis_context_fingerprint": "fingerprint",
    "analysis_workflow_state_id": "00000000-0000-4000-8000-000000000001",
}
ACTION_POLICY = cozymate_action_policy()


def test_milk_plan_event_is_persisted_once_and_applied_replay_is_side_effect_free() -> None:
    repository = MilkActionRepository()
    plan_service = TransactionalMilkPlanService(repository=repository)
    repository.action.apply_payload = {
        "title": "Private milk plan",
        "summary": "private supply and health context",
        "payload": {
            **MILK_ANALYSIS_LINEAGE,
            "start_date": "2026-07-04",
            "days": 2,
            "tasks": [{"title": "private task", "time": "08:00", "task_type": "pumping"}],
        },
    }
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={MILK_PLAN_CREATE_ACTION: MilkPlanCreateActionHandler(service=plan_service)},
    )

    first = asyncio.run(executor.apply(repository.action))
    replay = asyncio.run(executor.apply(repository.action))

    assert first.action.status == "applied"
    assert replay.replayed is True
    assert len(repository.domain_rows) == 3
    assert len([row for row in repository.domain_rows if isinstance(row, PlanTask)]) == 2
    assert [event.event_type for event in repository.events] == ["action.applied", MILK_PLAN_CHANGED_EVENT]
    changed = repository.events[-1]
    assert changed.thread_id == repository.run.thread_id
    assert changed.run_id == repository.run.id
    assert changed.payload["operation"] == "created"
    assert changed.payload["reason"] == "created"
    assert changed.payload["plan_type"] == "milk_management"
    assert changed.payload["source"] == "agent_action"
    assert changed.payload["affected_dates"] == ["2026-07-04", "2026-07-05"]
    assert changed.payload["action_id"] == str(repository.action.id)
    assert "private supply" not in str(changed.payload)
    assert "private task" not in str(changed.payload)


def test_milk_plan_event_failure_rolls_back_plan_and_success_events_before_durable_failure() -> None:
    repository = MilkActionRepository(failed_event_type=MILK_PLAN_CHANGED_EVENT)
    plan_service = TransactionalMilkPlanService(repository=repository)
    repository.action.apply_payload = {
        "title": "Milk plan",
        "payload": {
            **MILK_ANALYSIS_LINEAGE,
            "start_date": "2026-07-04",
            "days": 2,
            "tasks": [{"title": "吸奶", "time": "08:00", "task_type": "pumping"}],
        },
    }
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={MILK_PLAN_CREATE_ACTION: MilkPlanCreateActionHandler(service=plan_service)},
    )

    outcome = asyncio.run(executor.apply(repository.action))

    assert outcome.action.status == "failed"
    assert repository.domain_rows == []
    assert [event.event_type for event in repository.events] == ["action.failed"]
    assert MILK_PLAN_CHANGED_EVENT not in [event.event_type for event in repository.events]


def test_milk_plan_task_failure_rolls_back_the_plan_and_all_earlier_tasks() -> None:
    repository = MilkActionRepository()
    plan_service = FailingTransactionalMilkPlanService(repository=repository)
    repository.action.apply_payload = {
        "title": "Milk plan",
        "payload": {
            **MILK_ANALYSIS_LINEAGE,
            "start_date": "2026-07-04",
            "days": 1,
            "tasks": [
                {"title": "晨间吸奶", "time": "08:00", "task_type": "pumping"},
                {"title": "晚间吸奶", "time": "20:00", "task_type": "pumping"},
            ],
        },
    }
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={MILK_PLAN_CREATE_ACTION: MilkPlanCreateActionHandler(service=plan_service)},
    )

    outcome = asyncio.run(executor.apply(repository.action))

    assert outcome.action.status == "failed"
    assert repository.domain_rows == []
    assert [event.event_type for event in repository.events] == ["action.failed"]


def test_milk_schedule_reschedule_confirmation_replay_is_idempotent() -> None:
    repository = MilkActionRepository()
    repository.action.action_type = MILK_SCHEDULE_RESCHEDULE_ACTION
    plan_id = uuid4()
    task_id = uuid4()
    repository.action.apply_payload = {
        "plan_id": str(plan_id),
        "updates": [
            {
                "task_id": str(task_id),
                "expected_plan_id": str(plan_id),
                "expected_task_date": "2026-07-14",
                "expected_task_time": "11:00",
                "new_task_date": "2026-07-14",
                "new_task_time": "10:00",
            }
        ],
    }
    service = IdempotentRescheduleService(task_id=task_id, plan_id=plan_id)
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={MILK_SCHEDULE_RESCHEDULE_ACTION: MilkScheduleRescheduleActionHandler(service=service)},
    )

    first = asyncio.run(executor.apply(repository.action))
    replay = asyncio.run(executor.apply(repository.action))

    assert first.action.status == "applied"
    assert replay.replayed is True
    assert service.apply_count == 1
    assert [event.event_type for event in repository.events] == ["action.applied", MILK_PLAN_CHANGED_EVENT]
    assert repository.events[-1].payload["operation"] == "rescheduled"


class MilkActionRepository:
    def __init__(self, *, failed_event_type: str = "") -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="running",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v1",
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
            action_type=MILK_PLAN_CREATE_ACTION,
            target_type="plan",
            target_id="",
            status="confirmed",
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="idem",
            error_code="",
        )
        self.events: list[AgentEvent] = []
        self.domain_rows: list[Plan | PlanTask] = []
        self.failed_event_type = failed_event_type

    async def get_run_for_owner(self, *, run_id, owner_user_id):
        if run_id == self.run.id and owner_user_id == self.run.actor_user_id:
            return self.run
        return None

    async def get_action(self, *, action_id):
        return self.action if action_id == self.action.id else None

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

    async def append_event(self, **kwargs):
        if kwargs["event_type"] == self.failed_event_type:
            raise RuntimeError("event write failed")
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

    def begin_nested(self):
        return MilkSavepoint(self)


class TransactionalMilkPlanService:
    def __init__(self, *, repository: MilkActionRepository) -> None:
        self.repository = repository

    async def create_plan(self, **kwargs):
        plan = Plan(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_type=kwargs["plan_type"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            source=kwargs["source"],
            payload=kwargs["payload"],
        )
        self.repository.domain_rows.append(plan)
        return plan

    async def create_task(self, **kwargs):
        task = PlanTask(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_id=kwargs["plan_id"],
            task_date=kwargs["task_date"],
            task_time=kwargs["task_time"],
            title=kwargs["title"],
            description=kwargs["description"],
            status="pending",
            payload=kwargs["payload"],
        )
        self.repository.domain_rows.append(task)
        return task


class FailingTransactionalMilkPlanService(TransactionalMilkPlanService):
    def __init__(self, *, repository: MilkActionRepository) -> None:
        super().__init__(repository=repository)
        self.task_calls = 0

    async def create_task(self, **kwargs):
        self.task_calls += 1
        if self.task_calls == 2:
            raise ApiError(code="dependency_failed", message="task insert failed", status=503)
        return await super().create_task(**kwargs)


class IdempotentRescheduleService:
    def __init__(self, *, task_id, plan_id) -> None:
        self.task_id = task_id
        self.plan_id = plan_id
        self.apply_count = 0

    async def reschedule_milk_tasks(self, **kwargs):
        self.apply_count += 1
        return [
            PlanTask(
                id=self.task_id,
                owner_user_id=kwargs["owner_user_id"],
                plan_id=self.plan_id,
                task_date=date(2026, 7, 14),
                task_time="10:00",
                title="吸奶",
                status="pending",
                payload={"task_type": "pumping"},
            )
        ]


class MilkSavepoint:
    def __init__(self, repository: MilkActionRepository) -> None:
        self.repository = repository
        self.action_status = repository.action.status
        self.action_applied_at = repository.action.applied_at
        self.events = list(repository.events)
        self.domain_rows = list(repository.domain_rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, _exc, _traceback):
        if exc_type is not None:
            self.repository.action.status = self.action_status
            self.repository.action.applied_at = self.action_applied_at
            self.repository.events[:] = self.events
            self.repository.domain_rows[:] = self.domain_rows
        return False
