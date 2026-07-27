from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.records.agent_contracts import AgentLactationRecordApplyPayload
from app.modules.records.agent_router import (
    get_agent_lactation_read_service,
    get_agent_lactation_write_service,
)
from app.modules.records.agent_service import (
    AgentLactationReadService,
    AgentLactationRecordWriteService,
)


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_lactation_timeline_read_uses_service_identity_and_actor_scope() -> None:
    actor_user_id = uuid4()
    service = FakeAgentLactationReadService()
    app = _app()
    app.dependency_overrides[get_agent_lactation_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/lactation/timeline",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "as_of_date": "2026-07-26",
            "start_date": "2026-07-20",
            "end_date": "2026-07-26",
            "timezone_name": "Asia/Shanghai",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Shanghai"
    assert service.timeline_kwargs == {
        "owner_user_id": actor_user_id,
        "as_of_date": date(2026, 7, 26),
        "start_date": date(2026, 7, 20),
        "end_date": date(2026, 7, 26),
        "timezone_name": "Asia/Shanghai",
        "limit": 20,
    }


def test_agent_milk_analysis_snapshot_uses_explicit_actor_and_runtime_clock() -> None:
    actor_user_id = uuid4()
    service = FakeAgentLactationReadService()
    app = _app()
    app.dependency_overrides[get_agent_lactation_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/lactation/milk-analysis-snapshot",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "as_of_date": "2026-07-26",
            "timezone_name": "Asia/Shanghai",
            "days": 7,
            "limit": 8,
        },
    )

    assert response.status_code == 200
    assert response.json()["detail_level"] == "detailed"
    assert service.analysis_kwargs == {
        "owner_user_id": actor_user_id,
        "as_of_date": date(2026, 7, 26),
        "timezone_name": "Asia/Shanghai",
        "days": 7,
        "detail_limit": 8,
    }


def test_agent_lactation_routes_reject_missing_service_identity() -> None:
    response = TestClient(_app()).get(
        "/v1/internal/agent/lactation/timeline",
        params={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_lactation_apply_requires_action_bound_idempotency_key() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_lactation_write_service] = (
        lambda: FakeAgentLactationWriteService()
    )

    missing = TestClient(app).post(
        "/v1/internal/agent/actions/lactation.record/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=_apply_command(actor_user_id=actor_user_id, action_id=action_id),
    )
    wrong = TestClient(app).post(
        "/v1/internal/agent/actions/lactation.record/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=_apply_command(actor_user_id=actor_user_id, action_id=action_id),
    )

    assert missing.status_code == 422
    assert wrong.status_code == 422


def test_agent_lactation_apply_forwards_action_identity() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = FakeAgentLactationWriteService()
    app = _app()
    app.dependency_overrides[get_agent_lactation_write_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/lactation.record/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req-lactation",
        },
        json={
            "actor_user_id": str(actor_user_id),
            "action_id": str(action_id),
            "run_id": str(run_id),
            "payload": {
                "operation": "create",
                "item_type": "feeding",
                "infant_id": str(uuid4()),
                "occurred_at": "2026-07-26T08:00:00Z",
                "feed_type": "bottle",
                "volume_ml": 80,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["application_events"][0]["type"] == (
        "lactation.timeline.changed"
    )
    assert service.kwargs["owner_user_id"] == actor_user_id
    assert service.kwargs["action_id"] == action_id
    assert service.kwargs["run_id"] == run_id
    assert service.kwargs["idempotency_key"] == f"agent-action:{action_id}"
    assert service.kwargs["actor_service"] == "agent-runtime"


def test_agent_lactation_record_apply_contract_rejects_cross_item_fields() -> None:
    with pytest.raises(ValueError):
        AgentLactationRecordApplyPayload.model_validate(
            {
                "operation": "create",
                "item_type": "feeding",
                "occurred_at": "2026-07-26T08:00:00Z",
                "feed_type": "bottle",
                "milk_volume_ml": 80,
            }
        )


def test_agent_lactation_write_replay_does_not_repeat_business_write_or_audit() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    records_service = RecordingRecordsService(owner_user_id=actor_user_id)
    idempotency_service = InMemoryIdempotencyService()
    audit_service = RecordingAuditService()
    service = AgentLactationRecordWriteService(
        records_service=records_service,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "create",
            "item_type": "pumping",
            "occurred_at": "2026-07-26T08:00:00Z",
            "milk_volume_ml": 120,
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": run_id,
        "actor_service": "agent-runtime",
        "request_id": "req-lactation",
    }

    first = asyncio.run(service.apply_idempotent(**kwargs))
    replay = asyncio.run(service.apply_idempotent(**kwargs))

    assert first == replay
    assert records_service.create_pumping_calls == 1
    assert len(audit_service.calls) == 1
    assert audit_service.calls[0]["actor_user_id"] is None
    assert audit_service.calls[0]["details"]["owner_user_id"] == str(
        actor_user_id
    )


def test_agent_lactation_write_rejects_same_action_bound_to_another_run() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    service = AgentLactationRecordWriteService(
        records_service=RecordingRecordsService(owner_user_id=actor_user_id),
        idempotency_service=InMemoryIdempotencyService(),
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "delete",
            "item_type": "feeding",
            "record_id": str(uuid4()),
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "actor_service": "agent-runtime",
        "request_id": "",
    }

    asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))

    assert exc_info.value.code == "idempotency_conflict"


def test_agent_lactation_feeding_create_defaults_optional_infant_in_owner_scope() -> None:
    actor_user_id = uuid4()
    records_service = RecordingRecordsService(owner_user_id=actor_user_id)
    service = AgentLactationRecordWriteService(
        records_service=records_service,
        idempotency_service=InMemoryIdempotencyService(),
    )
    action_id = uuid4()

    asyncio.run(
        service.apply_idempotent(
            owner_user_id=actor_user_id,
            payload={
                "operation": "create",
                "item_type": "feeding",
                "occurred_at": "2026-07-26T08:00:00Z",
                "feed_type": " bottle ",
                "volume_ml": 80,
            },
            idempotency_key=f"agent-action:{action_id}",
            action_id=action_id,
            run_id=uuid4(),
            actor_service="agent-runtime",
            request_id="",
        )
    )

    assert records_service.create_feeding_kwargs["owner_user_id"] == actor_user_id
    assert records_service.create_feeding_kwargs["infant_id"] is None
    assert records_service.create_feeding_kwargs["feed_type"] == "bottle"


def test_agent_milk_analysis_snapshot_aggregates_all_owned_infant_records() -> None:
    actor_user_id = uuid4()
    records_service = AnalysisRecordsService(owner_user_id=actor_user_id)
    profile_service = AnalysisProfileService(owner_user_id=actor_user_id)
    service = AgentLactationReadService(
        timeline_service=SimpleNamespace(),
        records_service=records_service,
        profile_service=profile_service,
    )

    snapshot = asyncio.run(
        service.read_milk_analysis_snapshot(
            owner_user_id=actor_user_id,
            as_of_date=date(2026, 7, 26),
            timezone_name="Asia/Shanghai",
            days=7,
            detail_limit=8,
        )
    )

    assert snapshot.counts.infants == 2
    assert snapshot.counts.recent_feedings == 2
    assert {record.infant_id for record in snapshot.recent_feedings or []} == (
        set(records_service.infant_ids)
    )
    assert snapshot.volumes.recent_feeding_volume_ml == 150
    assert snapshot.volumes.recent_pumped_volume_ml == 170
    assert snapshot.status.data_coverage == "ready"
    assert snapshot.status.pumping_trend == "stable"
    assert snapshot.pumping_rhythm is not None
    assert snapshot.pumping_rhythm.timezone == "Asia/Shanghai"
    assert records_service.read_owner_ids == {actor_user_id}
    assert profile_service.read_owner_ids == {actor_user_id}


class FakeAgentLactationReadService:
    def __init__(self) -> None:
        self.timeline_kwargs: dict[str, object] = {}
        self.analysis_kwargs: dict[str, object] = {}

    async def read_timeline(self, **kwargs: object) -> dict[str, object]:
        self.timeline_kwargs = kwargs
        return {
            "as_of_date": kwargs["as_of_date"],
            "timezone": kwargs["timezone_name"],
            "start_date": kwargs["start_date"],
            "end_date": kwargs["end_date"],
            "items": [],
            "counts": {
                "pending": 0,
                "completed": 0,
                "skipped": 0,
                "recorded": 0,
            },
            "truncated": False,
        }

    async def read_milk_analysis_snapshot(
        self, **kwargs: object
    ) -> dict[str, object]:
        self.analysis_kwargs = kwargs
        return _analysis_snapshot(
            as_of_date=kwargs["as_of_date"],  # type: ignore[arg-type]
            timezone_name=str(kwargs["timezone_name"]),
        )


class FakeAgentLactationWriteService:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def apply_idempotent(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        record_id = uuid4()
        return SimpleNamespace(
            resource_type="feeding_record",
            resource_id=str(record_id),
            details={
                "operation": "created",
                "item_type": "feeding",
                "fields": ["feed_type", "infant_id", "occurred_at", "volume_ml"],
            },
            application_events=(
                {
                    "type": "lactation.timeline.changed",
                    "payload": {
                        "operation": "created",
                        "item_type": "feeding",
                        "record_id": str(record_id),
                        "source": "agent_action",
                    },
                },
            ),
        )


class RecordingRecordsService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.create_feeding_kwargs: dict[str, object] = {}
        self.create_pumping_calls = 0

    async def create_feeding(self, **kwargs: object) -> object:
        assert kwargs["owner_user_id"] == self.owner_user_id
        self.create_feeding_kwargs = kwargs
        return _record()

    async def create_pumping(self, **kwargs: object) -> object:
        assert kwargs["owner_user_id"] == self.owner_user_id
        self.create_pumping_calls += 1
        return _record()

    async def delete_feeding(self, **kwargs: object) -> None:
        assert kwargs["owner_user_id"] == self.owner_user_id


class AnalysisRecordsService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.read_owner_ids: set[UUID] = set()
        self.infant_ids = (uuid4(), uuid4())
        self.feedings = [
            _feeding(infant_id=self.infant_ids[0], hour=8, volume_ml=80),
            _feeding(infant_id=self.infant_ids[1], hour=7, volume_ml=70),
        ]
        self.pumpings = [
            _pumping(day=26, hour=6, volume_ml=90),
            _pumping(day=25, hour=6, volume_ml=80),
        ]

    async def list_feedings(self, **kwargs: object) -> list[object]:
        self._record_owner(kwargs)
        return self.feedings

    async def list_pumpings(self, **kwargs: object) -> list[object]:
        self._record_owner(kwargs)
        return self.pumpings

    async def list_growth(self, **kwargs: object) -> list[object]:
        self._record_owner(kwargs)
        return []

    async def get_milk_trends(self, **kwargs: object) -> object:
        self._record_owner(kwargs)
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    date=date(2026, 7, 25),
                    pumped_milk_volume_ml=80,
                    pumping_count=1,
                    measured_only=True,
                ),
                SimpleNamespace(
                    date=date(2026, 7, 26),
                    pumped_milk_volume_ml=90,
                    pumping_count=1,
                    measured_only=True,
                ),
            ]
        )

    def _record_owner(self, kwargs: dict[str, object]) -> None:
        owner_user_id = kwargs["owner_user_id"]
        assert isinstance(owner_user_id, UUID)
        assert owner_user_id == self.owner_user_id
        self.read_owner_ids.add(owner_user_id)


class AnalysisProfileService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.read_owner_ids: set[UUID] = set()

    async def list_infants(self, **kwargs: object) -> list[object]:
        owner_user_id = kwargs["owner_user_id"]
        assert isinstance(owner_user_id, UUID)
        assert owner_user_id == self.owner_user_id
        self.read_owner_ids.add(owner_user_id)
        return [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]


class InMemoryIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    async def reserve(self, **kwargs: object) -> object:
        key = str(kwargs["key"])
        existing = self.records.get(key)
        if existing is not None:
            if existing.request_hash != kwargs["request_hash"]:
                raise ApiError(
                    code="idempotency_conflict",
                    message="conflict",
                    status=409,
                )
            return SimpleNamespace(status="replay", record=existing)
        record = SimpleNamespace(
            request_hash=kwargs["request_hash"],
            response_ref="",
            status="in_progress",
        )
        self.records[key] = record
        return SimpleNamespace(status="reserved", record=record)

    async def mark_completed(
        self,
        *,
        record: SimpleNamespace,
        response_ref: str,
    ) -> object:
        record.status = "completed"
        record.response_ref = response_ref
        return record


class RecordingAuditService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )


def _apply_command(*, actor_user_id: UUID, action_id: UUID) -> dict[str, object]:
    return {
        "actor_user_id": str(actor_user_id),
        "action_id": str(action_id),
        "run_id": str(uuid4()),
        "payload": {
            "operation": "delete",
            "item_type": "feeding",
            "record_id": str(uuid4()),
        },
    }


def _analysis_snapshot(
    *,
    as_of_date: date,
    timezone_name: str,
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "timezone": timezone_name,
        "detail_level": "detailed",
        "window": {"days": 7, "limit": 8, "include_today": True},
        "status": {
            "data_coverage": "no_recent_data",
            "pumping_trend": "insufficient_data",
            "measured_only": True,
        },
        "counts": {
            "infants": 0,
            "recent_feedings": 0,
            "recent_pumpings": 0,
            "trend_days": 7,
            "days_with_pumping": 0,
            "trend_pumping_count": 0,
            "recent_growth": 0,
        },
        "volumes": {
            "recent_feeding_volume_ml": 0,
            "recent_pumped_volume_ml": 0,
            "trend_pumped_volume_ml": 0,
            "average_daily_pumped_volume_ml": 0,
        },
        "latest": {"feeding_at": None, "pumping_at": None},
        "observation_flags": [
            "no_infant_profile",
            "no_recent_feeding_records",
            "no_recent_pumping_records",
            "no_pumping_trend_data",
        ],
        "recent_feedings": [],
        "recent_pumpings": [],
        "pumping_rhythm": {
            "timezone": timezone_name,
            "representative_date": None,
            "representative_times": [],
        },
        "recent_growth": [],
        "pumping_trends": [
            {
                "date": as_of_date,
                "pumped_milk_volume_ml": 0,
                "pumping_count": 0,
                "measured_only": True,
            }
        ],
        "analysis": {
            "pathway": "补充宝宝资料后再判断供需",
            "data_coverage": "no_recent_data",
            "pumping_trend": "insufficient_data",
            "has_recent_growth": False,
            "missing_inputs": [
                "no_infant_profile",
                "no_recent_feeding_records",
                "no_recent_pumping_records",
                "no_pumping_trend_data",
            ],
            "recommended_next_step": "先确认宝宝资料或体重/尿布等摄入信号。",
        },
    }


def _record() -> object:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return SimpleNamespace(id=uuid4(), created_at=now, updated_at=now)


def _feeding(*, infant_id: UUID, hour: int, volume_ml: float) -> object:
    return SimpleNamespace(
        id=uuid4(),
        infant_id=infant_id,
        feed_time=datetime(2026, 7, 26, hour, tzinfo=timezone.utc),
        feed_type="bottle",
        feed_action="feed",
        volume_ml=volume_ml,
        duration_seconds=None,
        title="Feeding",
    )


def _pumping(*, day: int, hour: int, volume_ml: float) -> object:
    return SimpleNamespace(
        id=uuid4(),
        pump_start_time=datetime(
            2026,
            7,
            day,
            hour,
            tzinfo=timezone.utc,
        ),
        pump_end_time=None,
        milk_volume_ml=volume_ml,
        duration_seconds=900,
        pump_type="electric",
        source="device",
        title="Pumping",
    )
