import asyncio
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.service import DiaryService


def test_diary_service_keeps_create_and_update_as_distinct_operations() -> None:
    owner_user_id = uuid4()
    repository = FakeDiaryRepository(owner_user_id=owner_user_id)
    audit = FakeAuditService()
    service = DiaryService(repository=repository, audit_service=audit)

    created = asyncio.run(
        service.create_entry(
            owner_user_id=owner_user_id,
            entry_date=date(2026, 7, 11),
            values={"content": "今天胎动规律。"},
            request_id="create-request",
        )
    )
    updated = asyncio.run(
        service.update_entry(
            owner_user_id=owner_user_id,
            entry_date=created.entry_date,
            values={"mood": "安心"},
            request_id="update-request",
        )
    )

    assert updated is created
    assert created.content == "今天胎动规律。"
    assert created.mood == "安心"
    assert [item["action"] for item in audit.entries] == [
        "pregnancy_diary.entry.create",
        "pregnancy_diary.entry.update",
    ]


def test_diary_service_update_requires_an_existing_active_entry() -> None:
    service = DiaryService(repository=FakeDiaryRepository(owner_user_id=uuid4()))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_entry(
                owner_user_id=uuid4(),
                entry_date=date(2026, 7, 11),
                values={"content": "不存在的日记"},
            )
        )

    assert exc_info.value.code == "not_found"


def test_diary_service_maps_concurrent_create_to_conflict() -> None:
    service = DiaryService(repository=ConcurrentCreateRepository())  # type: ignore[arg-type]

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_entry(
                owner_user_id=uuid4(),
                entry_date=date(2026, 7, 11),
                values={"content": "并发写入"},
            )
        )

    assert exc_info.value.code == "conflict"
    assert exc_info.value.status == 409


class FakeDiaryRepository:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.entry: PregnancyDiaryEntry | None = None

    async def create_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        self.entry = PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            symptom_tags=[],
            attachments=[],
            status="active",
        )
        for key, value in values.items():
            setattr(self.entry, key, value)
        return self.entry

    async def update_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        if self.entry is None or self.entry.owner_user_id != owner_user_id or self.entry.entry_date != entry_date:
            return None
        for key, value in values.items():
            setattr(self.entry, key, value)
        return self.entry


class FakeAuditService:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)


class ConcurrentCreateRepository:
    async def create_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        raise IntegrityError("INSERT", {}, Exception("duplicate owner/date"))
