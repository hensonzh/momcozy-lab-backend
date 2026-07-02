import asyncio
from datetime import date
from uuid import UUID, uuid4

from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.service import DiaryService


def test_diary_service_upserts_entry_and_records_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeDiaryRepository()
    audit_service = FakeAuditService()
    service = DiaryService(repository=repository, audit_service=audit_service)

    entry = asyncio.run(
        service.upsert_entry(
            owner_user_id=owner_user_id,
            entry_date=date(2026, 7, 2),
            values={"mood": "calm", "content": "A good day"},
            request_id="req_diary",
        )
    )

    assert entry.owner_user_id == owner_user_id
    assert entry.mood == "calm"
    assert audit_service.record_kwargs["action"] == "diary.entry.upsert"


def test_diary_service_lists_and_deletes_entries() -> None:
    owner_user_id = uuid4()
    entry = _entry(owner_user_id=owner_user_id)
    repository = FakeDiaryRepository(entry=entry, entries=[entry])
    audit_service = FakeAuditService()
    service = DiaryService(repository=repository, audit_service=audit_service)

    entries = asyncio.run(service.list_entries(owner_user_id=owner_user_id, limit=10))
    asyncio.run(service.delete_entry(owner_user_id=owner_user_id, entry_date=entry.entry_date, request_id="req_delete"))

    assert entries == [entry]
    assert entry.status == "deleted"
    assert audit_service.record_kwargs["action"] == "diary.entry.delete"


def _entry(*, owner_user_id: UUID) -> PregnancyDiaryEntry:
    return PregnancyDiaryEntry(
        id=uuid4(),
        owner_user_id=owner_user_id,
        entry_date=date(2026, 7, 2),
        mood="calm",
        status="active",
        symptom_tags=[],
        attachments=[],
    )


class FakeDiaryRepository:
    def __init__(self, *, entry=None, entries=None) -> None:
        self.entry = entry
        self.entries = entries or []

    async def get_entry_by_date(self, *, owner_user_id: UUID, entry_date: date):
        return self.entry

    async def list_entries(self, **kwargs):
        return self.entries

    async def upsert_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        entry = self.entry or _entry(owner_user_id=owner_user_id)
        entry.entry_date = entry_date
        for field, value in values.items():
            setattr(entry, field, value)
        self.entry = entry
        return entry

    async def soft_delete_entry(self, **kwargs):
        if self.entry is None:
            return None
        self.entry.status = "deleted"
        self.entry.deleted_at = kwargs["deleted_at"]
        return self.entry


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
