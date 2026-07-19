import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.agent_runtime.models import AgentAction
from app.modules.records.agent_actions import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    FeedingRecordCreateActionHandler,
    FeedingRecordDeleteActionHandler,
    GrowthRecordCreateActionHandler,
    GrowthRecordUpdateActionHandler,
    PumpingRecordCreateActionHandler,
)
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from app.workers.errors import PermanentJobError


def test_feeding_record_create_action_handler_creates_record_through_service() -> None:
    infant_id = uuid4()
    service = FakeRecordsService()
    action = _action(
        action_type=FEEDING_RECORD_CREATE_ACTION,
        target_type="feeding_record",
        apply_payload={
            "infant_id": str(infant_id),
            "feed_time": "2026-07-04T08:30:00Z",
            "feed_type": "bottle",
            "feed_action": "left",
            "volume_ml": 90,
            "title": "Morning feed",
        },
    )

    result = asyncio.run(FeedingRecordCreateActionHandler(service=service)(action))

    assert result.resource_type == "feeding_record"
    assert result.resource_id == str(service.feeding_record.id)
    assert result.details == {"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)}
    assert service.feeding_kwargs["owner_user_id"] == action.actor_user_id
    assert service.feeding_kwargs["infant_id"] == infant_id
    assert service.feeding_kwargs["feed_time"] == datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc)
    assert service.feeding_kwargs["feed_type"] == "bottle"
    assert service.feeding_kwargs["volume_ml"] == 90.0
    assert service.feeding_kwargs["idempotency_key"] == "idem-action"


def test_feeding_record_create_action_handler_rejects_missing_quantity() -> None:
    action = _action(
        action_type=FEEDING_RECORD_CREATE_ACTION,
        target_type="feeding_record",
        apply_payload={
            "feed_time": "2026-07-04T08:30:00Z",
            "feed_type": "bottle",
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(FeedingRecordCreateActionHandler(service=FakeRecordsService())(action))

    assert exc_info.value.code == "missing_feeding_quantity"


def test_feeding_record_create_action_handler_rejects_invalid_infant_id() -> None:
    action = _action(
        action_type=FEEDING_RECORD_CREATE_ACTION,
        target_type="feeding_record",
        apply_payload={
            "infant_id": "not-a-uuid",
            "feed_time": "2026-07-04T08:30:00Z",
            "feed_type": "bottle",
            "duration_seconds": 600,
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(FeedingRecordCreateActionHandler(service=FakeRecordsService())(action))

    assert exc_info.value.code == "invalid_infant_id"


def test_pumping_record_create_action_handler_creates_record_through_service() -> None:
    service = FakeRecordsService()
    action = _action(
        action_type=PUMPING_RECORD_CREATE_ACTION,
        target_type="pumping_record",
        apply_payload={
            "pump_start_time": "2026-07-04T09:00:00+00:00",
            "pump_end_time": "2026-07-04T09:20:00+00:00",
            "milk_volume_ml": 120.5,
            "pump_type": "electric",
            "source": "agent",
            "title": "Morning pump",
        },
    )

    result = asyncio.run(PumpingRecordCreateActionHandler(service=service)(action))

    assert result.resource_type == "pumping_record"
    assert result.resource_id == str(service.pumping_record.id)
    assert result.details == {"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)}
    assert service.pumping_kwargs["owner_user_id"] == action.actor_user_id
    assert service.pumping_kwargs["pump_start_time"] == datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc)
    assert service.pumping_kwargs["pump_end_time"] == datetime(2026, 7, 4, 9, 20, tzinfo=timezone.utc)
    assert service.pumping_kwargs["milk_volume_ml"] == 120.5
    assert service.pumping_kwargs["source"] == "agent"
    assert service.pumping_kwargs["idempotency_key"] == "idem-action"


def test_pumping_record_create_action_handler_rejects_invalid_start_time() -> None:
    action = _action(
        action_type=PUMPING_RECORD_CREATE_ACTION,
        target_type="pumping_record",
        apply_payload={
            "pump_start_time": "tomorrow morning",
            "milk_volume_ml": 120,
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(PumpingRecordCreateActionHandler(service=FakeRecordsService())(action))

    assert exc_info.value.code == "invalid_pump_start_time"


def test_pumping_record_create_action_handler_rejects_negative_quantity() -> None:
    action = _action(
        action_type=PUMPING_RECORD_CREATE_ACTION,
        target_type="pumping_record",
        apply_payload={
            "pump_start_time": "2026-07-04T09:00:00Z",
            "milk_volume_ml": -1,
        },
    )

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(PumpingRecordCreateActionHandler(service=FakeRecordsService())(action))

    assert exc_info.value.code == "invalid_milk_volume_ml"


def test_feeding_record_delete_action_handler_deletes_record_through_service() -> None:
    service = FakeRecordsService()
    record_id = uuid4()
    action = _action(
        action_type=FEEDING_RECORD_DELETE_ACTION,
        target_type="feeding_record",
        apply_payload={"record_id": str(record_id)},
    )

    result = asyncio.run(FeedingRecordDeleteActionHandler(service=service)(action))

    assert result.resource_type == "feeding_record"
    assert result.resource_id == str(record_id)
    assert service.delete_feeding_kwargs["owner_user_id"] == action.actor_user_id
    assert service.delete_feeding_kwargs["record_id"] == record_id


def test_growth_record_create_action_handler_creates_growth_through_service() -> None:
    infant_id = uuid4()
    service = FakeRecordsService()
    action = _action(
        action_type=GROWTH_RECORD_CREATE_ACTION,
        target_type="growth_record",
        apply_payload={
            "infant_id": str(infant_id),
            "measured_at": "2026-07-04T10:00:00Z",
            "height_cm": 62.5,
            "weight_kg": 6.4,
        },
    )

    result = asyncio.run(GrowthRecordCreateActionHandler(service=service)(action))

    assert result.resource_type == "growth_record"
    assert result.resource_id == str(service.growth_record.id)
    assert service.create_growth_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_growth_kwargs["infant_id"] == infant_id
    assert service.create_growth_kwargs["measured_at"] == datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
    assert service.create_growth_kwargs["height_cm"] == 62.5
    assert service.create_growth_kwargs["weight_kg"] == 6.4
    assert service.create_growth_kwargs["idempotency_key"] == "idem-action"


def test_growth_record_update_action_handler_updates_growth_through_service() -> None:
    service = FakeRecordsService()
    record_id = uuid4()
    action = _action(
        action_type=GROWTH_RECORD_UPDATE_ACTION,
        target_type="growth_record",
        apply_payload={"record_id": str(record_id), "weight_kg": 6.5},
    )

    result = asyncio.run(GrowthRecordUpdateActionHandler(service=service)(action))

    assert result.resource_type == "growth_record"
    assert result.resource_id == str(service.growth_record.id)
    assert result.details["fields"] == ["weight_kg"]
    assert service.update_growth_kwargs["owner_user_id"] == action.actor_user_id
    assert service.update_growth_kwargs["record_id"] == record_id
    assert service.update_growth_kwargs["updates"] == {"weight_kg": 6.5}


def test_growth_record_create_action_handler_rejects_missing_measurement() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            GrowthRecordCreateActionHandler(service=FakeRecordsService())(
                _action(
                    action_type=GROWTH_RECORD_CREATE_ACTION,
                    target_type="growth_record",
                    apply_payload={"measured_at": "2026-07-04T10:00:00Z"},
                )
            )
        )

    assert exc_info.value.code == "missing_growth_measurement"


class FakeRecordsService:
    def __init__(self) -> None:
        self.feeding_record = FeedingRecord(
            id=uuid4(),
            owner_user_id=uuid4(),
            infant_id=None,
            feed_time=datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc),
            feed_type="bottle",
            feed_action="left",
            volume_ml=90.0,
            duration_seconds=None,
            title="Morning feed",
        )
        self.pumping_record = PumpingRecord(
            id=uuid4(),
            owner_user_id=uuid4(),
            pump_start_time=datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc),
            pump_end_time=datetime(2026, 7, 4, 9, 20, tzinfo=timezone.utc),
            milk_volume_ml=120.5,
            pump_type="electric",
            duration_seconds=None,
            source="agent",
            title="Morning pump",
        )
        self.growth_record = GrowthRecord(
            id=uuid4(),
            owner_user_id=uuid4(),
            infant_id=None,
            measured_at=datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc),
            height_cm=62.5,
            weight_kg=6.4,
            head_cm=None,
        )
        self.feeding_kwargs = {}
        self.pumping_kwargs = {}
        self.delete_feeding_kwargs = {}
        self.create_growth_kwargs = {}
        self.update_growth_kwargs = {}

    async def create_feeding(self, **kwargs):
        self.feeding_kwargs = kwargs
        self.feeding_record.owner_user_id = kwargs["owner_user_id"]
        self.feeding_record.infant_id = kwargs["infant_id"]
        self.feeding_record.feed_time = kwargs["feed_time"]
        self.feeding_record.feed_type = kwargs["feed_type"]
        self.feeding_record.feed_action = kwargs["feed_action"]
        self.feeding_record.volume_ml = kwargs["volume_ml"]
        self.feeding_record.duration_seconds = kwargs["duration_seconds"]
        self.feeding_record.title = kwargs["title"]
        return self.feeding_record

    async def create_pumping(self, **kwargs):
        self.pumping_kwargs = kwargs
        self.pumping_record.owner_user_id = kwargs["owner_user_id"]
        self.pumping_record.pump_start_time = kwargs["pump_start_time"]
        self.pumping_record.pump_end_time = kwargs["pump_end_time"]
        self.pumping_record.milk_volume_ml = kwargs["milk_volume_ml"]
        self.pumping_record.pump_type = kwargs["pump_type"]
        self.pumping_record.duration_seconds = kwargs["duration_seconds"]
        self.pumping_record.source = kwargs["source"]
        self.pumping_record.title = kwargs["title"]
        return self.pumping_record

    async def delete_feeding(self, **kwargs):
        self.delete_feeding_kwargs = kwargs

    async def create_growth(self, **kwargs):
        self.create_growth_kwargs = kwargs
        self.growth_record.owner_user_id = kwargs["owner_user_id"]
        self.growth_record.infant_id = kwargs["infant_id"]
        self.growth_record.measured_at = kwargs["measured_at"]
        self.growth_record.height_cm = kwargs["height_cm"]
        self.growth_record.weight_kg = kwargs["weight_kg"]
        self.growth_record.head_cm = kwargs["head_cm"]
        return self.growth_record

    async def update_growth(self, **kwargs):
        self.update_growth_kwargs = kwargs
        for key, value in kwargs["updates"].items():
            setattr(self.growth_record, key, value)
        return self.growth_record


def _action(*, action_type: str, target_type: str, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=action_type,
        target_type=target_type,
        target_id="",
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
