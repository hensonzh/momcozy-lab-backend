import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql

from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.repository import DiaryRepository
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


def test_diary_repository_upsert_restores_soft_deleted_entry() -> None:
    owner_user_id = uuid4()
    entry_date = date(2026, 7, 2)
    deleted_entry = _entry(owner_user_id=owner_user_id)
    deleted_entry.entry_date = entry_date
    deleted_entry.status = "deleted"
    deleted_entry.deleted_at = datetime(2026, 7, 3, tzinfo=timezone.utc)
    session = FakeDiarySession(entry=deleted_entry)
    repository = DiaryRepository(session=session)  # type: ignore[arg-type]

    entry = asyncio.run(
        repository.upsert_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values={"mood": "calm again", "content": "Restored entry"},
        )
    )

    assert entry is deleted_entry
    assert entry.status == "active"
    assert entry.deleted_at is None
    assert entry.mood == "calm again"
    assert session.added == []
    assert session.flushed is True


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


class FakeDiarySession:
    def __init__(self, *, entry: PregnancyDiaryEntry) -> None:
        self.entry = entry
        self.added = []
        self.flushed = False

    async def scalar(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "deleted_at IS NULL" in sql and self.entry.deleted_at is not None:
            return None
        return self.entry

    def add(self, instance) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed = True


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
