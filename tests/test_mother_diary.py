import asyncio
from datetime import date
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
from app.modules.auth import CurrentUser
from app.modules.mother.models import MotherDiaryEntry
from app.modules.mother.repository import MotherDiaryRepository
from app.modules.mother.router import get_mother_diary_service
from app.modules.mother.schemas import MotherDiaryWrite
from app.modules.mother.service import MotherDiaryService
from app.modules.users.models import User


def test_diary_rejects_old_fields_and_empty_or_invalid_reports():
    for diary in [{}, {"sleep_minutes": 240}, {"lactation": {"amount_ml": 60}},
                  {"rest": {"total": "eight-hours"}}, {"mood": {"score": 90}},
                  {"body": {"note": "   "}}, {"body": {"severity": "mild"}},
                  {"mood": {"pressures": ["unclear", "sleep-loss"]}}]:
        with pytest.raises(ValidationError):
            MotherDiaryWrite.model_validate({"expected_version": 0, "diary": diary})


def test_owner_version_and_idempotent_save_are_persisted():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__, MotherDiaryEntry.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, other = uuid4(), uuid4()
        audit = AsyncMock()
        day = date(2026, 9, 1)
        initial = MotherDiaryWrite.model_validate({"expected_version": 0, "diary": {"rest": {"total": "4-5h"}}})
        async with sessions() as session:
            session.add_all([User(id=owner), User(id=other)])
            await session.commit()
            service = MotherDiaryService(MotherDiaryRepository(session), audit)
            saved = await service.save(owner, day, initial, "request-1")
            record_id = saved.id
            assert saved.version == 1
            await session.commit()
        async with sessions() as session:
            service = MotherDiaryService(MotherDiaryRepository(session), audit)
            replay = await service.save(owner, day, initial, "retry")
            assert replay.id == record_id and replay.version == 1
            assert await service.list(other, day, day) == []
            stale = MotherDiaryWrite.model_validate({"expected_version": 0, "diary": {"mood": {"tone": "steady"}}})
            with pytest.raises(ApiError) as conflict:
                await service.save(owner, day, stale, "stale")
            assert conflict.value.code == "version_conflict"
            update = MotherDiaryWrite.model_validate({"expected_version": 1, "diary": {
                "rest": {"total": "4-5h"}, "mood": {"tone": "steady"}}})
            saved = await service.save(owner, day, update, "request-2")
            assert saved.id == record_id and saved.version == 2
            await session.commit()
        async with sessions() as session:
            service = MotherDiaryService(MotherDiaryRepository(session), audit)
            results = await service.list(owner, day, day)
            assert results[0].diary["rest"]["total"] == "4-5h"
            assert results[0].diary["mood"]["tone"] == "steady"
            assert results[0].version == 2
        assert audit.record.await_count == 2
        assert "steady" not in str(audit.record.call_args_list)
        await engine.dispose()
    asyncio.run(run())


def test_route_requires_auth_and_does_not_accept_a_patient_owner():
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)
    assert client.get("/v1/mother/diary?start=2026-09-01&end=2026-09-08").status_code == 401
    owner = uuid4()
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        user_id=owner, subject=str(owner), session_id="test", token_id="test", roles=frozenset({"user"}), permissions=frozenset())
    service = AsyncMock()
    service.list.return_value = []
    app.dependency_overrides[get_mother_diary_service] = lambda: service
    response = client.get("/v1/mother/diary?start=2026-09-01&end=2026-09-08&owner_user_id=another")
    assert response.status_code == 200
    assert service.list.call_args.args[0] == owner
    response = client.put("/v1/mother/diary/2026-09-01", json={
        "owner_user_id": str(uuid4()), "expected_version": 0, "diary": {"mood": {"tone": "steady"}}})
    assert response.status_code == 422
    service.save.assert_not_called()
