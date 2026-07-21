import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.agents.cozymate.actions import cozymate_action_policy
from app.core.errors import ApiError
from app.agent_runtime.actions.executor import (
    AgentActionApplyResult,
    AgentActionExecutor,
)
from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agents.cozymate.actions.plans import (
    PREGNANCY_PLAN_CREATE_ACTION,
    PregnancyPlanCreateActionHandler,
)
from app.modules.plans.models import Plan
from app.agents.cozymate.actions.records import FEEDING_RECORD_CREATE_ACTION, FeedingRecordCreateActionHandler
from app.modules.records.models import FeedingRecord
from tests.test_agent_runtime_service import FakeAgentRuntimeRepository


PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"
ACTION_POLICY = cozymate_action_policy()


def test_confirmation_only_authorizes_and_requeues_same_run_without_domain_apply() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="notifications.milk_reminder.create",
            target_type="notification",
            preview_payload={"summary": "Pump does not turn on"},
            apply_payload={"issue_summary": "Pump does not turn on"},
        )
    )
    run.status = "waiting_for_confirmation"

    confirmed = asyncio.run(
        service.confirm_action(
            owner_user_id=owner_user_id,
            action_id=action.id,
            edited_apply_payload={"issue_summary": "Pump does not turn on after charging"},
            idempotency_key="idem-action",
        )
    )

    assert confirmed.status == "confirmed"
    assert confirmed.idempotency_key == "idem-action"
    assert confirmed.apply_payload == {"issue_summary": "Pump does not turn on after charging"}
    assert run.status == "queued"
    assert [event.event_type for event in repository.events][-3:] == [
        "action.confirmation_required",
        "action.confirmed",
        "run.queued",
    ]
    confirmation = repository.events[-3]
    assert confirmation.payload["preview_payload"] == {"summary": "Pump does not turn on"}
    assert confirmation.payload["requires_confirmation"] is True
    assert confirmation.payload["confirmation_policy"] == "always"
    assert confirmation.payload["user_visible"] is True
    assert "apply_payload" not in confirmation.payload
    assert repository.events[-2].payload["action_status"] == "confirmed"
    queued_payload = repository.events[-1].payload
    assert {key: queued_payload[key] for key in ("reason", "action_id", "phase")} == {
        "reason": "action_confirmed",
        "action_id": str(action.id),
        "phase": "queued",
    }
    assert queued_payload["semantic"]["label"] == "我已经收到你的消息啦～"


def test_duplicate_confirmation_is_idempotent_and_does_not_requeue_twice() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="notifications.milk_reminder.create")
    )
    run.status = "waiting_for_confirmation"

    first = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))
    event_count = len(repository.events)
    second = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id, idempotency_key="retry"))

    assert first is second
    assert second.status == "confirmed"
    assert len(repository.events) == event_count
    assert [event.event_type for event in repository.events].count("action.confirmed") == 1
    assert [event.event_type for event in repository.events].count("run.queued") == 2  # initial run plus resume


@pytest.mark.parametrize("action_type", ["plans.milk_plan.create", "plans.milk_schedule.reschedule"])
def test_milk_actions_reject_confirmation_payload_edits(action_type: str) -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create milk plan"))
    original_payload = {"title": "稳奶计划", "payload": {"direction": "maintain"}}
    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=action_type,
            target_type="plan",
            apply_payload=original_payload,
        )
    )
    run.status = "waiting_for_confirmation"

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.confirm_action(
                owner_user_id=owner_user_id,
                action_id=action.id,
                edited_apply_payload={"title": "追奶计划", "payload": {"direction": "increase"}},
            )
        )

    assert exc_info.value.code == "action_payload_edit_not_allowed"
    assert action.status == "confirmation_required"
    assert action.apply_payload == original_payload
    assert run.status == "waiting_for_confirmation"


def test_cross_owner_cannot_read_or_confirm_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="notifications.milk_reminder.create")
    )
    run.status = "waiting_for_confirmation"

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.confirm_action(owner_user_id=uuid4(), action_id=action.id))

    assert exc_info.value.code == "not_found"
    assert action.status == "confirmation_required"


def test_reject_and_expire_finish_waiting_run_without_apply() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    rejected_action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="notifications.milk_reminder.create")
    )
    run.status = "waiting_for_confirmation"
    rejected = asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=rejected_action.id, reason="not now"))

    assert rejected.status == "rejected"
    assert run.status == "completed"
    assert repository.events[-2].payload["user_visible"] is True
    assert repository.events[-1].payload["reason"] == "action_rejected"
    assert repository.events[-1].payload["action_id"] == str(rejected.id)
    assert repository.events[-1].payload["semantic"]["surface"] == "hidden"

    run.status = "completed"
    second_run = asyncio.run(
        service.create_run(actor_user_id=owner_user_id, thread_id=repository.thread.id, message="Create another ticket")
    )
    expired_action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=second_run.id,
            action_type="notifications.milk_reminder.create",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    second_run.status = "waiting_for_confirmation"
    expired = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=expired_action.id))

    assert expired.status == "expired"
    assert second_run.status == "completed"
    assert repository.events[-2].event_type == "action.expired"
    assert repository.events[-1].payload["reason"] == "action_expired"
    assert repository.events[-1].payload["action_id"] == str(expired.id)
    assert repository.events[-1].payload["semantic"]["surface"] == "hidden"


def test_direct_action_requires_in_process_executor_before_persisting() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Track feeding"))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.propose_action(
                owner_user_id=owner_user_id,
                run_id=run.id,
                action_type=FEEDING_RECORD_CREATE_ACTION,
                target_type="feeding_record",
                side_effect_level="low",
            )
        )

    assert exc_info.value.code == "action_executor_not_configured"
    assert repository.action is None


@pytest.mark.parametrize(
    ("action_type", "target_type", "side_effect_level"),
    [
        ("pregnancy.plan.create", "plan", "medium"),
        ("pregnancy.plan_todo.update", "plan", "medium"),
        ("plans.plan.delete", "plan", "medium"),
        ("hospital_bag.cart.update", "hospital_bag_cart", "low"),
        ("records.feeding_record.create", "feeding_record", "low"),
        ("records.feeding_record.delete", "feeding_record", "medium"),
        ("records.pumping_record.delete", "pumping_record", "medium"),
        ("records.growth_record.update", "growth_record", "medium"),
        ("records.growth_record.delete", "growth_record", "medium"),
        ("plans.task.create", "plan_task", "medium"),
        ("plans.task.complete", "plan_task", "medium"),
        ("plans.task.update", "plan_task", "medium"),
        ("plans.task.delete", "plan_task", "medium"),
        ("support.ticket.create", "support_ticket", "medium"),
    ],
)
def test_explicit_intent_actions_apply_synchronously_without_confirmation_card(
    action_type: str,
    target_type: str,
    side_effect_level: str,
) -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        return AgentActionApplyResult(resource_type=target_type, resource_id="resource-1")

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={action_type: apply})
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository, action_executor=executor)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Do the exact action"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=action_type,
            target_type=target_type,
            side_effect_level=side_effect_level,
        )
    )

    assert action.status == "applied"
    assert "action.confirmation_required" not in [event.event_type for event in repository.events]
    assert "action.queued" not in [event.event_type for event in repository.events]
    applied = repository.events[-1]
    assert applied.event_type == "action.applied"
    assert applied.payload["requires_confirmation"] is False
    assert applied.payload["confirmation_policy"] == "explicit_intent"
    assert applied.payload["user_visible"] is False


@pytest.mark.parametrize(
    ("action_type", "target_type"),
    [
        ("plans.milk_plan.create", "plan"),
        ("notifications.milk_reminder.create", "notification"),
    ],
)
def test_value_bearing_preview_actions_still_require_one_confirmation(action_type: str, target_type: str) -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Prepare action"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=action_type,
            target_type=target_type,
            side_effect_level="medium",
        )
    )

    assert action.status == "confirmation_required"
    assert repository.events[-1].event_type == "action.confirmation_required"


def test_direct_feeding_apply_and_idempotent_replay_do_not_duplicate_domain_write() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    records_service = FakeAgentRecordsService()
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={FEEDING_RECORD_CREATE_ACTION: FeedingRecordCreateActionHandler(service=records_service)},
    )
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository, action_executor=executor)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Add a 90 ml bottle feeding"))
    kwargs = {
        "owner_user_id": owner_user_id,
        "run_id": run.id,
        "action_type": FEEDING_RECORD_CREATE_ACTION,
        "target_type": "feeding_record",
        "side_effect_level": "low",
        "apply_payload": {
            "feed_time": "2026-07-04T08:30:00Z",
            "feed_type": "bottle",
            "volume_ml": 90,
            "title": "Morning bottle",
        },
        "idempotency_key": "idem-milk-action",
    }

    first, created = asyncio.run(service.propose_action_once(**kwargs))
    second, replay_created = asyncio.run(service.propose_action_once(**kwargs))

    assert created is True and replay_created is False
    assert second.id == first.id and second.status == "applied"
    assert len(records_service.feedings) == 1
    assert records_service.feedings[0].owner_user_id == owner_user_id
    assert records_service.feedings[0].feed_time == datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc)
    assert [event.event_type for event in repository.events].count("action.applied") == 1


def test_pregnancy_plan_applies_synchronously_and_changed_event_replays_once() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    plans_service = FakeAgentPlansService()
    executor = AgentActionExecutor(action_policy=ACTION_POLICY,
        repository=repository,
        handlers={PREGNANCY_PLAN_CREATE_ACTION: PregnancyPlanCreateActionHandler(service=plans_service)},
    )
    service = AgentRuntimeService(action_policy=ACTION_POLICY, repository=repository, action_executor=executor)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create a third trimester plan"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=PREGNANCY_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            apply_payload={
                "title": "Third trimester plan",
                "summary": "Prepare appointments and bag tasks.",
                "payload": {"gestational_week": 32},
            },
            idempotency_key="pregnancy-plan-1",
        )
    )
    replay, created = asyncio.run(
        service.propose_action_once(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=PREGNANCY_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            apply_payload=action.apply_payload,
            idempotency_key="pregnancy-plan-1",
        )
    )

    assert action.status == "applied"
    assert replay.id == action.id and created is False
    assert len(plans_service.plans) == 1
    assert plans_service.plans[0].plan_type == "pregnancy"
    event_types = [event.event_type for event in repository.events]
    assert event_types == ["run.queued", "message.completed", "action.applied", PREGNANCY_PLAN_CHANGED_EVENT]
    assert "action.confirmation_required" not in event_types
    assert "action.queued" not in event_types
    changed = repository.events[-1]
    assert changed.payload["plan_id"] == str(plans_service.plans[0].id)
    assert changed.payload["action_id"] == str(action.id)
    assert changed.payload["user_visible"] is False
    replayed = asyncio.run(service.list_events(owner_user_id=owner_user_id, run_id=run.id, after_sequence=changed.sequence - 1, limit=10))
    assert replayed == [changed]


class FakeActionRepository(FakeAgentRuntimeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.action = None
        self.actions = []
        self.locked_run_ids = []

    def begin_nested(self):
        return _NoopSavepoint()

    async def create_action(self, **kwargs):
        self.action = AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["actor_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs["target_id"],
            status=kwargs["status"],
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            expires_at=kwargs["expires_at"],
            error_code="",
        )
        self.actions.append(self.action)
        return self.action

    async def lock_run_for_action_proposal(self, *, run_id):
        self.locked_run_ids.append(run_id)

    async def get_reusable_action_by_idempotency_key(self, *, run_id, actor_user_id, action_type, idempotency_key):
        return next(
            (
                action
                for action in reversed(self.actions)
                if action.run_id == run_id
                and action.actor_user_id == actor_user_id
                and action.action_type == action_type
                and action.idempotency_key == idempotency_key
                and action.status in {"confirmation_required", "proposed", "confirmed", "applying", "applied"}
            ),
            None,
        )

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID):
        return next((action for action in self.actions if action.id == action_id and action.actor_user_id == owner_user_id), None)

    async def get_action(self, *, action_id: UUID):
        return next((action for action in self.actions if action.id == action_id), None)

    async def get_run(self, *, run_id: UUID):
        return next((run for run in self.runs if run.id == run_id), None)

    async def mark_run_queued(self, *, run):
        run.status = "queued"
        run.started_at = None
        return run

    async def mark_action_confirmed(self, **kwargs):
        action = kwargs["action"]
        action.status = "confirmed"
        action.confirmed_at = kwargs["confirmed_at"]
        if kwargs["apply_payload"] is not None:
            action.apply_payload = kwargs["apply_payload"]
        action.idempotency_key = kwargs["idempotency_key"]
        return action

    async def mark_action_rejected(self, **kwargs):
        action = kwargs["action"]
        action.status = "rejected"
        action.failed_at = kwargs["failed_at"]
        action.error_code = kwargs["error_code"]
        return action

    async def mark_action_expired(self, **kwargs):
        action = kwargs["action"]
        action.status = "expired"
        action.failed_at = kwargs["failed_at"]
        action.error_code = kwargs["error_code"]
        return action

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


class _NoopSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class FakeAgentRecordsService:
    def __init__(self) -> None:
        self.feedings = []

    async def create_feeding(self, **kwargs):
        record = FeedingRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            infant_id=kwargs["infant_id"],
            feed_time=kwargs["feed_time"],
            feed_type=kwargs["feed_type"],
            feed_action=kwargs["feed_action"],
            volume_ml=kwargs["volume_ml"],
            duration_seconds=kwargs["duration_seconds"],
            title=kwargs["title"],
            status="active",
        )
        self.feedings.append(record)
        return record


class FakeAgentPlansService:
    def __init__(self) -> None:
        self.plans = []

    async def create_plan(self, **kwargs):
        plan = Plan(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_type=kwargs["plan_type"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            source=kwargs["source"],
            payload=kwargs["payload"],
            status="active",
        )
        self.plans.append(plan)
        return plan
