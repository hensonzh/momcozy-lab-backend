import asyncio
from datetime import date
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.service import DiaryService


def test_today_diary_main_flow_restores_soft_deleted_entry() -> None:
    owner_user_id = uuid4()
    entry_date = date(2026, 7, 3)
    repository = InMemoryDiaryRepository()
    audit_service = FlowAuditService()
    service = DiaryService(repository=repository, audit_service=audit_service)

    with pytest.raises(ApiError) as empty_exc:
        asyncio.run(service.get_entry(owner_user_id=owner_user_id, entry_date=entry_date))
    assert empty_exc.value.code == "not_found"

    created = asyncio.run(
        service.create_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values={"mood": "calm", "content": "Packed the hospital bag."},
            request_id="req_diary_create",
        )
    )
    visible_entries = asyncio.run(service.list_entries(owner_user_id=owner_user_id, limit=10))

    assert [entry.id for entry in visible_entries] == [created.id]
    assert created.status == "active"
    assert created.mood == "calm"

    asyncio.run(service.delete_entry(owner_user_id=owner_user_id, entry_date=entry_date, request_id="req_diary_delete"))

    with pytest.raises(ApiError) as deleted_exc:
        asyncio.run(service.get_entry(owner_user_id=owner_user_id, entry_date=entry_date))
    assert deleted_exc.value.code == "not_found"
    assert asyncio.run(service.list_entries(owner_user_id=owner_user_id, limit=10)) == []

    restored = asyncio.run(
        service.create_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values={"mood": "hopeful", "content": "Restored today's note."},
            request_id="req_diary_restore",
        )
    )
    visible_after_restore = asyncio.run(service.list_entries(owner_user_id=owner_user_id, limit=10))

    assert restored.id == created.id
    assert restored.status == "active"
    assert restored.deleted_at is None
    assert restored.mood == "hopeful"
    assert [entry.id for entry in visible_after_restore] == [created.id]
    assert [entry["action"] for entry in audit_service.entries] == [
        "pregnancy_diary.entry.create",
        "pregnancy_diary.entry.delete",
        "pregnancy_diary.entry.create",
    ]


class InMemoryDiaryRepository:
    def __init__(self) -> None:
        self.entries: list[PregnancyDiaryEntry] = []

    async def get_entry_by_date(self, *, owner_user_id: UUID, entry_date: date, include_deleted: bool = False):
        return next(
            (
                entry
                for entry in self.entries
                if entry.owner_user_id == owner_user_id and entry.entry_date == entry_date and (include_deleted or entry.deleted_at is None)
            ),
            None,
        )

    async def list_entries(self, *, owner_user_id: UUID, start_date, end_date, limit: int):
        entries = [
            entry
            for entry in self.entries
            if entry.owner_user_id == owner_user_id
            and entry.status == "active"
            and entry.deleted_at is None
            and (start_date is None or entry.entry_date >= start_date)
            and (end_date is None or entry.entry_date <= end_date)
        ]
        return sorted(entries, key=lambda entry: entry.entry_date, reverse=True)[:limit]

    async def create_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        entry = await self.get_entry_by_date(owner_user_id=owner_user_id, entry_date=entry_date, include_deleted=True)
        if entry is None:
            entry = PregnancyDiaryEntry(
                id=uuid4(),
                owner_user_id=owner_user_id,
                entry_date=entry_date,
                status="active",
                symptom_tags=[],
                attachments=[],
            )
            self.entries.append(entry)
        elif entry.deleted_at is None:
            return None
        entry.status = "active"
        entry.deleted_at = None
        for field, value in values.items():
            setattr(entry, field, value)
        return entry

    async def update_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict,
        content_mode: str = "replace",
    ):
        entry = await self.get_entry_by_date(owner_user_id=owner_user_id, entry_date=entry_date)
        if entry is None:
            return None
        for field, value in values.items():
            setattr(entry, field, value)
        return entry

    async def soft_delete_entry(self, *, owner_user_id: UUID, entry_date: date, deleted_at):
        entry = await self.get_entry_by_date(owner_user_id=owner_user_id, entry_date=entry_date)
        if entry is None:
            return None
        entry.status = "deleted"
        entry.deleted_at = deleted_at
        return entry


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
