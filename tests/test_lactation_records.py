import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.db.base import Base
from app.modules.audit.models import IdempotencyKey
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import IdempotencyService
from app.modules.auth import CurrentUser
from app.modules.lactation.models import LactationRecord
from app.modules.lactation.repository import LactationRepository
from app.modules.lactation.router import get_lactation_service
from app.modules.lactation.schemas import LactationUpdate, LactationWrite
from app.modules.lactation.service import LactationService
from app.modules.users.models import User

WHEN = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
PUMP = {"method": "pump", "side": "left", "occurred_at": WHEN.isoformat(), "volume_ml": 75}


@pytest.mark.parametrize("changes", [
    {"volume_ml": -1}, {"volume_ml": float("inf")}, {"volume_ml": 2001},
    {"side": "both"}, {"duration_minutes": 10}, {"amount": 80},
    {"occurred_at": "2026-09-01T07:00:00"},
    {"occurred_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"method": "nurse", "volume_ml": 40},
])
def test_lactation_rejects_ambiguous_and_invalid_measurements(changes):
    with pytest.raises(ValidationError):
        LactationWrite.model_validate({"observation": {**PUMP, **changes}})


def test_persistent_lifecycle_owner_scope_idempotency_and_measurement_switch():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, LactationRecord.__table__, IdempotencyKey.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, other = uuid4(), uuid4()
        audit_service = AsyncMock()
        initial = LactationWrite.model_validate({"observation": PUMP})
        async with sessions() as session:
            session.add_all([User(id=owner), User(id=other)])
            await session.commit()
            audit = AuditRepository(session)
            service = LactationService(LactationRepository(session), audit_service, IdempotencyService(repository=audit))
            saved = await service.create(owner, initial.observation, "same-write", "create")
            record_id = saved.id
            await session.commit()
        async with sessions() as session:
            audit = AuditRepository(session)
            service = LactationService(LactationRepository(session), audit_service, IdempotencyService(repository=audit))
            replay = await service.create(owner, initial.observation, "same-write", "retry")
            assert replay.id == record_id and replay.version == 1
            with pytest.raises(ApiError) as reused:
                await service.create(owner, initial.observation.model_copy(update={"volume_ml": 80}), "same-write", "conflicting-key")
            assert reused.value.code == "idempotency_conflict"
            assert await service.list(other, WHEN, WHEN + timedelta(days=1)) == []
            assert len(await service.list(owner, WHEN, WHEN + timedelta(days=1))) == 1
            assert await service.list(owner, WHEN - timedelta(hours=1), WHEN) == []
            with pytest.raises(ApiError) as forbidden:
                await service.set_deleted(other, record_id, 1, True, "other-owner")
            assert forbidden.value.status == 404
            switched = LactationUpdate.model_validate({"expected_version": 1, "observation": {
                "method": "nurse", "side": "right", "occurred_at": WHEN.isoformat(), "duration_minutes": 12}})
            updated = await service.update(owner, record_id, switched, "switch-method")
            assert updated.volume_ml is None and updated.duration_minutes == 12 and updated.version == 2
            retry_update = await service.update(owner, record_id, switched, "retry-update")
            assert retry_update.version == 2
            with pytest.raises(ApiError) as stale:
                await service.set_deleted(owner, record_id, 1, True, "stale-delete")
            assert stale.value.code == "version_conflict"
            deleted = await service.set_deleted(owner, record_id, 2, True, "delete")
            assert deleted.version == 3
            assert await service.list(owner, WHEN, WHEN + timedelta(days=1)) == []
            retry = await service.set_deleted(owner, record_id, 2, True, "retry-delete")
            assert retry.version == 3
            restored = await service.set_deleted(owner, record_id, 3, False, "undo")
            assert restored.version == 4 and restored.deleted_at is None
            retry = await service.set_deleted(owner, record_id, 3, False, "retry-undo")
            assert retry.version == 4
            await session.commit()
        async with sessions() as session:
            records = await LactationRepository(session).list(owner, WHEN, WHEN + timedelta(days=1))
            assert len(records) == 1 and records[0].method == "nurse" and records[0].version == 4
            audits = audit_service.record.call_args_list
            assert [item.kwargs["action"] for item in audits] == ["lactation.create", "lactation.update", "lactation.delete", "lactation.restore"]
            assert all(set(item.kwargs["details"]) == {"version"} for item in audits)
        await engine.dispose()
    asyncio.run(run())


def test_api_requires_current_user_and_version_on_deletion():
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)
    assert client.get("/v1/lactation/records?start=2026-09-01T00:00:00Z&end=2026-09-02T00:00:00Z").status_code == 401
    owner, record_id = uuid4(), uuid4()
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        user_id=owner, subject=str(owner), session_id="test", token_id="test", roles=frozenset({"user"}), permissions=frozenset())
    service = AsyncMock()
    app.dependency_overrides[get_lactation_service] = lambda: service
    assert client.post("/v1/lactation/records", json={"observation": PUMP}).status_code == 422
    assert client.post("/v1/lactation/records", headers={"Idempotency-Key": "create"}, json={
        "owner_user_id": str(uuid4()), "observation": PUMP}).status_code == 422
    assert client.delete(f"/v1/lactation/records/{record_id}").status_code == 422
    service.create.assert_not_called()
    service.set_deleted.assert_not_called()
    service.set_deleted.return_value = LactationRecord(id=record_id, owner_user_id=owner, version=2)
    deleted = client.delete(f"/v1/lactation/records/{record_id}", headers={"If-Match": "1"})
    assert deleted.status_code == 200 and deleted.json() == {"id": str(record_id), "version": 2}
    assert service.set_deleted.call_args.args[:4] == (owner, record_id, 1, True)
