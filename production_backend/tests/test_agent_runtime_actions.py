import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.modules.agent_runtime.actions.outbox import AgentActionOutboxHandler
from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB, AgentRuntimeService
from production_backend.app.modules.plans.agent_actions import PREGNANCY_PLAN_CREATE_ACTION, PregnancyPlanCreateActionHandler
from production_backend.app.modules.plans.models import Plan
from production_backend.app.modules.records.agent_actions import FEEDING_RECORD_CREATE_ACTION, FeedingRecordCreateActionHandler
from production_backend.app.modules.records.models import FeedingRecord
from production_backend.tests.test_agent_runtime_service import FakeAgentRuntimeRepository


def test_agent_runtime_actions_confirm_to_action_queued_without_queued_status() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="support.ticket.create",
            target_type="support_ticket",
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
    assert confirmed.status != "queued"
    assert confirmed.idempotency_key == "idem-action"
    assert confirmed.apply_payload["issue_summary"] == "Pump does not turn on after charging"
    proposal_event = repository.events[-2]
    assert proposal_event.event_type == "action.confirmation_required"
    assert proposal_event.payload["action_id"] == str(action.id)
    assert proposal_event.payload["action_status"] == "confirmation_required"
    assert proposal_event.payload["action_type"] == "support.ticket.create"
    assert proposal_event.payload["target_type"] == "support_ticket"
    assert proposal_event.payload["side_effect_level"] == "medium"
    assert proposal_event.payload["preview_payload"] == {"summary": "Pump does not turn on"}
    assert "apply_payload" not in proposal_event.payload
    queued_event = repository.events[-1]
    assert queued_event.event_type == "action.queued"
    assert queued_event.payload["action_status"] == "confirmed"
    assert queued_event.payload["action_type"] == "support.ticket.create"
    assert queued_event.payload["target_type"] == "support_ticket"
    assert queued_event.payload["outbox_status"] == "queued"
    assert queued_event.payload["outbox_job_id"] == str(outbox_service.job.id)
    assert repository.run.status == "waiting_for_confirmation"
    assert repository.events[-1].event_type == "action.queued"
    assert outbox_service.enqueue_kwargs["job_type"] == AGENT_ACTION_APPLY_JOB
    assert outbox_service.enqueue_kwargs["payload"]["action_id"] == str(action.id)
    assert outbox_service.enqueue_kwargs["idempotency_key"] == f"agent-action:{action.id}:apply"
    assert outbox_service.enqueue_kwargs["action_id"] == action.id


def test_agent_runtime_actions_reject_confirmation_required_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create"))
    run.status = "waiting_for_confirmation"

    rejected = asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="not now"))

    assert rejected.status == "rejected"
    assert rejected.error_code == "rejected_by_user"
    rejected_event = repository.events[-2]
    assert rejected_event.event_type == "action.rejected"
    assert rejected_event.payload["action_status"] == "rejected"
    assert rejected_event.payload["action_type"] == "support.ticket.create"
    assert rejected_event.payload["reason"] == "not now"
    assert repository.run.status == "completed"
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload == {"reason": "action_rejected", "action_id": str(action.id)}


def test_agent_runtime_actions_do_not_reject_confirmed_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create"))
    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))
    event_count = len(repository.events)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="too late"))

    assert exc_info.value.code == "conflict"
    assert confirmed.status == "confirmed"
    assert repository.action.status == "confirmed"
    assert len(repository.events) == event_count


def test_agent_runtime_actions_do_not_overwrite_terminal_rejection_states() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create"))
    action.status = "expired"
    action.error_code = "action_expired"
    event_count = len(repository.events)

    rejected = asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="too late"))

    assert rejected.status == "expired"
    assert rejected.error_code == "action_expired"
    assert len(repository.events) == event_count


def test_agent_runtime_actions_generate_action_idempotency_key_for_confirmation() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create"))

    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    generated_key = f"agent-action:{action.id}"
    outbox_key = f"agent-action:{action.id}:apply"
    assert confirmed.idempotency_key == generated_key
    assert outbox_service.enqueue_kwargs["idempotency_key"] == outbox_key


def test_agent_runtime_actions_scope_outbox_idempotency_key_to_each_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    first_run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create first ticket"))
    first_action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=first_run.id, action_type="support.ticket.create")
    )
    first_run.status = "waiting_for_confirmation"
    asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=first_action.id, idempotency_key="shared-key"))
    first_run.status = "completed"
    second_run = asyncio.run(
        service.create_run(actor_user_id=owner_user_id, thread_id=repository.thread.id, message="Create second ticket")
    )
    second_action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=second_run.id, action_type="support.ticket.create")
    )
    second_run.status = "waiting_for_confirmation"

    asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=second_action.id, idempotency_key="shared-key"))

    assert first_action.idempotency_key == "shared-key"
    assert second_action.idempotency_key == "shared-key"
    assert [call["idempotency_key"] for call in outbox_service.enqueue_calls] == [
        f"agent-action:{first_action.id}:apply",
        f"agent-action:{second_action.id}:apply",
    ]


def test_agent_runtime_actions_expire_past_confirmation_without_enqueueing() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="support.ticket.create",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    run.status = "waiting_for_confirmation"

    expired = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    assert expired.status == "expired"
    assert expired.error_code == "action_expired"
    assert outbox_service.enqueue_kwargs == {}
    assert repository.events[-2].event_type == "action.expired"
    assert repository.events[-2].payload["action_status"] == "expired"
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload == {"reason": "action_expired", "action_id": str(action.id)}


def test_agent_runtime_actions_reject_unsupported_action_type_before_persisting() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Delete my device"))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="device.delete"))

    assert exc_info.value.code == "unsupported_agent_action"
    assert repository.action is None


def test_agent_runtime_actions_require_outbox_for_direct_apply_before_persisting() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Track feeding"))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.propose_action(
                owner_user_id=owner_user_id,
                run_id=run.id,
                action_type=FEEDING_RECORD_CREATE_ACTION,
                target_type="feeding_record",
                side_effect_level="low",
                apply_payload={"feed_time": "2026-07-04T08:30:00Z", "feed_type": "bottle"},
            )
        )

    assert exc_info.value.code == "outbox_not_configured"
    assert repository.action is None


def test_agent_runtime_actions_accept_hospital_bag_cart_update_policy() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Update hospital bag cart"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="hospital_bag.cart.update",
            target_type="hospital_bag_cart",
            side_effect_level="low",
            preview_payload={"summary": "Mark nursing bra packed"},
            apply_payload={"cart_update": {"set_checked": [{"item_id": "nursing-bra", "checked": True}]}},
        )
    )
    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    assert confirmed.status == "confirmed"
    assert confirmed.action_type == "hospital_bag.cart.update"
    assert confirmed.target_type == "hospital_bag_cart"
    assert confirmed.side_effect_level == "low"
    assert "action.confirmation_required" not in [event.event_type for event in repository.events]
    assert repository.events[-1].event_type == "action.queued"
    assert repository.events[-1].payload["action_status"] == "confirmed"
    assert repository.events[-1].payload["action_type"] == "hospital_bag.cart.update"
    assert repository.events[-1].payload["target_type"] == "hospital_bag_cart"
    assert outbox_service.enqueue_kwargs["payload"]["apply_payload"] == {
        "cart_update": {"set_checked": [{"item_id": "nursing-bra", "checked": True}]}
    }


def test_agent_runtime_actions_accept_milk_plan_policy() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create a milk plan"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="plans.milk_plan.create",
            target_type="plan",
            side_effect_level="medium",
            preview_payload={"title": "Increase pumping consistency"},
            apply_payload={"title": "Increase pumping consistency"},
        )
    )
    run.status = "waiting_for_confirmation"
    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    assert confirmed.status == "confirmed"
    assert confirmed.action_type == "plans.milk_plan.create"
    assert confirmed.target_type == "plan"
    assert confirmed.side_effect_level == "medium"
    assert repository.events[-2].payload["action_type"] == "plans.milk_plan.create"
    assert repository.events[-1].event_type == "action.queued"
    assert outbox_service.enqueue_kwargs["payload"]["apply_payload"] == {"title": "Increase pumping consistency"}


def test_agent_runtime_actions_accept_pregnancy_plan_and_task_policies() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create pregnancy plan tasks"))
    cases = [
        (
            "pregnancy.plan.create",
            "plan",
            {"title": "Third trimester plan"},
            {"title": "Third trimester plan"},
        ),
        (
            "plans.task.create",
            "plan_task",
            {"title": "Book prenatal appointment"},
            {"title": "Book prenatal appointment"},
        ),
        (
            "plans.task.complete",
            "plan_task",
            {"task_id": "task_1", "completed": True},
            {"task_id": "task_1", "completed": True},
        ),
    ]

    for action_type, target_type, preview_payload, apply_payload in cases:
        action = asyncio.run(
            service.propose_action(
                owner_user_id=owner_user_id,
                run_id=run.id,
                action_type=action_type,
                target_type=target_type,
                side_effect_level="medium",
                preview_payload=preview_payload,
                apply_payload=apply_payload,
            )
        )
        run.status = "waiting_for_confirmation"
        confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

        assert confirmed.status == "confirmed"
        assert confirmed.action_type == action_type
        assert confirmed.target_type == target_type
        assert confirmed.side_effect_level == "medium"
        assert repository.events[-2].payload["action_type"] == action_type
        assert repository.events[-1].event_type == "action.queued"
        assert outbox_service.enqueue_kwargs["payload"]["apply_payload"] == apply_payload


def test_agent_runtime_actions_accept_milk_reminder_policy() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create a milk reminder"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="notifications.milk_reminder.create",
            target_type="notification",
            side_effect_level="medium",
            preview_payload={"title": "Time to pump"},
            apply_payload={"title": "Time to pump"},
        )
    )
    run.status = "waiting_for_confirmation"
    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    assert confirmed.status == "confirmed"
    assert confirmed.action_type == "notifications.milk_reminder.create"
    assert confirmed.target_type == "notification"
    assert confirmed.side_effect_level == "medium"
    assert repository.events[-2].payload["action_type"] == "notifications.milk_reminder.create"
    assert repository.events[-1].event_type == "action.queued"
    assert outbox_service.enqueue_kwargs["payload"]["apply_payload"] == {"title": "Time to pump"}


def test_agent_runtime_actions_auto_queue_pregnancy_diary_create() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Save my diary"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="pregnancy_diary.entry.create",
            target_type="pregnancy_diary_entry",
            side_effect_level="low",
            preview_payload={"entry_date": "2026-07-04", "fields": ["content"]},
            apply_payload={"entry_date": "2026-07-04", "values": {"content": "Today I felt steady."}},
        )
    )

    assert action.status == "confirmed"
    assert action.action_type == "pregnancy_diary.entry.create"
    assert action.target_type == "pregnancy_diary_entry"
    assert action.side_effect_level == "low"
    assert repository.events[-1].event_type == "action.queued"
    assert outbox_service.enqueue_kwargs["payload"]["apply_payload"] == {
        "entry_date": "2026-07-04",
        "values": {"content": "Today I felt steady."},
    }


def test_agent_runtime_actions_require_confirmation_for_pregnancy_diary_delete() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository, outbox_service=FakeOutboxService())
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Delete my diary"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="pregnancy_diary.entry.delete",
            target_type="pregnancy_diary_entry",
            side_effect_level="medium",
            preview_payload={"entry_date": "2026-07-04"},
            apply_payload={"entry_date": "2026-07-04"},
        )
    )

    assert action.status == "confirmation_required"
    assert repository.events[-1].event_type == "action.confirmation_required"


def test_agent_milk_feeding_main_flow_confirms_applies_and_replays_events() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    records_service = FakeAgentRecordsService()
    runtime_service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)

    run = asyncio.run(
        runtime_service.create_run(
            actor_user_id=owner_user_id,
            thread_id=None,
            message="Add a 90 ml bottle feeding for 8:30.",
            request_id="req_milk_agent",
            trace_id="trace_milk_agent",
        )
    )
    action = asyncio.run(
        runtime_service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=FEEDING_RECORD_CREATE_ACTION,
            target_type="feeding_record",
            side_effect_level="low",
            preview_payload={
                "feed_time": "2026-07-04T08:30:00Z",
                "feed_type": "bottle",
                "volume_ml": 90,
            },
            apply_payload={
                "feed_time": "2026-07-04T08:30:00Z",
                "feed_type": "bottle",
                "volume_ml": 90,
                "title": "Morning bottle",
            },
            idempotency_key="idem-milk-action",
        )
    )

    confirmed = asyncio.run(
        runtime_service.confirm_action(
            owner_user_id=owner_user_id,
            action_id=action.id,
            idempotency_key="idem-milk-action",
        )
    )
    handler = AgentActionOutboxHandler(
        repository=repository,
        handlers={FEEDING_RECORD_CREATE_ACTION: FeedingRecordCreateActionHandler(service=records_service)},
    )
    asyncio.run(handler(outbox_service.job))
    asyncio.run(runtime_service.confirm_action(owner_user_id=owner_user_id, action_id=action.id, idempotency_key="retry-key"))
    asyncio.run(handler(outbox_service.job))

    applied_event = repository.events[-1]

    assert confirmed.status == "applied"
    assert outbox_service.enqueue_calls[-1]["idempotency_key"] == f"agent-action:{action.id}:apply"
    assert len(outbox_service.enqueue_calls) == 1
    assert len(records_service.feedings) == 1
    assert records_service.feedings[0].owner_user_id == owner_user_id
    assert records_service.feedings[0].feed_time == datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc)
    assert records_service.create_feeding_kwargs["idempotency_key"] == "idem-milk-action"
    assert [event.event_type for event in repository.events] == [
        "run.queued",
        "message.completed",
        "action.queued",
        "action.applied",
    ]
    assert applied_event.payload["action_id"] == str(action.id)
    assert applied_event.payload["action_status"] == "applied"
    assert applied_event.payload["resource_type"] == "feeding_record"
    assert applied_event.payload["resource_id"] == str(records_service.feedings[0].id)
    assert applied_event.payload["details"]["agent_action_id"] == str(action.id)
    assert "apply_payload" not in repository.events[2].payload


def test_agent_pregnancy_plan_main_flow_confirms_applies_and_replays_events() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    plans_service = FakeAgentPlansService()
    runtime_service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)

    run = asyncio.run(
        runtime_service.create_run(
            actor_user_id=owner_user_id,
            thread_id=None,
            message="Create a third trimester plan.",
            request_id="req_pregnancy_agent",
            trace_id="trace_pregnancy_agent",
        )
    )
    action = asyncio.run(
        runtime_service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=PREGNANCY_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            preview_payload={
                "title": "Third trimester plan",
                "summary": "Prepare appointments and bag tasks.",
            },
            apply_payload={
                "title": "Third trimester plan",
                "summary": "Prepare appointments and bag tasks.",
                "payload": {"gestational_week": 32},
            },
        )
    )
    run.status = "waiting_for_confirmation"

    confirmed = asyncio.run(runtime_service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))
    handler = AgentActionOutboxHandler(
        repository=repository,
        handlers={PREGNANCY_PLAN_CREATE_ACTION: PregnancyPlanCreateActionHandler(service=plans_service)},
    )
    asyncio.run(handler(outbox_service.job))
    asyncio.run(runtime_service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))
    asyncio.run(handler(outbox_service.job))

    applied_event = repository.events[-2]

    assert confirmed.status == "applied"
    assert len(outbox_service.enqueue_calls) == 1
    assert len(plans_service.plans) == 1
    assert plans_service.plans[0].owner_user_id == owner_user_id
    assert plans_service.plans[0].plan_type == "pregnancy"
    assert plans_service.create_plan_kwargs["idempotency_key"] == f"agent-action:{action.id}"
    assert [event.event_type for event in repository.events] == [
        "run.queued",
        "message.completed",
        "action.confirmation_required",
        "action.queued",
        "action.applied",
        "run.completed",
    ]
    assert applied_event.payload["action_id"] == str(action.id)
    assert applied_event.payload["action_status"] == "applied"
    assert applied_event.payload["resource_type"] == "plan"
    assert applied_event.payload["resource_id"] == str(plans_service.plans[0].id)
    assert applied_event.payload["details"]["plan_type"] == "pregnancy"
    assert "apply_payload" not in repository.events[2].payload


class FakeActionRepository(FakeAgentRuntimeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.action = None
        self.actions = []

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

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID):
        return next((action for action in self.actions if action.id == action_id and action.actor_user_id == owner_user_id), None)

    async def get_action(self, *, action_id: UUID):
        return next((action for action in self.actions if action.id == action_id), None)

    async def get_run(self, *, run_id: UUID):
        return next((run for run in self.runs if run.id == run_id), None)

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


class FakeOutboxService:
    def __init__(self) -> None:
        self.job = OutboxJob(
            id=uuid4(),
            job_type=AGENT_ACTION_APPLY_JOB,
            status="queued",
            payload={},
            idempotency_key="",
            request_id="",
            trace_id="",
        )
        self.enqueue_kwargs = {}
        self.enqueue_calls = []

    async def enqueue(self, **kwargs):
        self.job = OutboxJob(
            id=uuid4(),
            job_type=AGENT_ACTION_APPLY_JOB,
            status="queued",
            payload={},
            idempotency_key="",
            request_id="",
            trace_id="",
        )
        self.enqueue_kwargs = kwargs
        self.enqueue_calls.append(kwargs)
        self.job.action_id = kwargs["action_id"]
        self.job.payload = kwargs["payload"]
        self.job.idempotency_key = kwargs["idempotency_key"]
        return self.job


class FakeAgentRecordsService:
    def __init__(self) -> None:
        self.feedings = []
        self.create_feeding_kwargs = {}

    async def create_feeding(self, **kwargs):
        self.create_feeding_kwargs = kwargs
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
        self.create_plan_kwargs = {}

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
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
