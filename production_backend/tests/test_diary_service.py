import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.repository import DiaryEntryMutation, DiaryRepository
from production_backend.app.modules.diary.service import DiaryService


def test_diary_service_creates_entry_and_records_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeDiaryRepository()
    audit_service = FakeAuditService()
    service = DiaryService(repository=repository, audit_service=audit_service)

    entry = asyncio.run(
        service.create_entry(
            owner_user_id=owner_user_id,
            entry_date=date(2026, 7, 2),
            values={"mood": "calm", "content": "A good day"},
            request_id="req_diary",
        )
    )

    assert entry.owner_user_id == owner_user_id
    assert entry.mood == "calm"
    assert audit_service.record_kwargs["action"] == "pregnancy_diary.entry.create"


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
    assert audit_service.record_kwargs["action"] == "pregnancy_diary.entry.delete"


def test_diary_service_appends_content_under_repository_lock() -> None:
    owner_user_id = uuid4()
    entry = _entry(owner_user_id=owner_user_id)
    entry.content = "Existing fact"
    repository = FakeDiaryRepository(entry=entry)
    service = DiaryService(repository=repository)

    updated = asyncio.run(
        service.update_entry(
            owner_user_id=owner_user_id,
            entry_date=entry.entry_date,
            values={"content": "New fact"},
            content_mode="append",
        )
    )

    assert updated.content == "Existing fact\nNew fact"
    assert repository.update_content_mode == "append"


def test_diary_service_append_is_idempotent_for_replayed_suffix() -> None:
    owner_user_id = uuid4()
    entry = _entry(owner_user_id=owner_user_id)
    entry.content = "Existing fact\nNew fact"
    repository = FakeDiaryRepository(entry=entry)
    audit_service = FakeAuditService()
    service = DiaryService(repository=repository, audit_service=audit_service)

    mutation = asyncio.run(
        service.update_entry_with_status(
            owner_user_id=owner_user_id,
            entry_date=entry.entry_date,
            values={"content": "New fact"},
            content_mode="append",
        )
    )

    assert mutation.entry.content == "Existing fact\nNew fact"
    assert mutation.changed is False
    assert audit_service.record_kwargs == {}


def test_diary_repository_create_restores_soft_deleted_entry_without_stale_values() -> None:
    owner_user_id = uuid4()
    entry_date = date(2026, 7, 2)
    deleted_entry = _entry(owner_user_id=owner_user_id)
    deleted_entry.entry_date = entry_date
    deleted_entry.status = "deleted"
    deleted_entry.deleted_at = datetime(2026, 7, 3, tzinfo=timezone.utc)
    deleted_entry.appointment_note = "old question"
    session = FakeDiarySession(entry=deleted_entry)
    repository = DiaryRepository(session=session)  # type: ignore[arg-type]

    entry = asyncio.run(
        repository.create_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values={"mood": "calm again", "content": "Restored entry"},
        )
    )

    assert entry is deleted_entry
    assert entry.status == "active"
    assert entry.deleted_at is None
    assert entry.mood == "calm again"
    assert entry.appointment_note == ""
    assert session.added == []
    assert session.flushed is True
    assert session.refreshed == [(deleted_entry, ("updated_at",))]
    assert session.nested_transactions == 1
    assert session.selects_for_update == 1


def test_diary_repository_update_refreshes_server_generated_updated_at() -> None:
    owner_user_id = uuid4()
    entry = _entry(owner_user_id=owner_user_id)
    session = FakeDiarySession(entry=entry)
    repository = DiaryRepository(session=session)  # type: ignore[arg-type]

    mutation = asyncio.run(
        repository.update_entry_with_status(
            owner_user_id=owner_user_id,
            entry_date=entry.entry_date,
            values={"mood": "hopeful"},
        )
    )

    assert mutation is not None
    assert mutation.changed is True
    assert mutation.entry.mood == "hopeful"
    assert session.refreshed == [(entry, ("updated_at",))]


def test_diary_repository_delete_locks_row_and_refreshes_updated_at() -> None:
    owner_user_id = uuid4()
    entry = _entry(owner_user_id=owner_user_id)
    session = FakeDiarySession(entry=entry)
    repository = DiaryRepository(session=session)  # type: ignore[arg-type]

    deleted = asyncio.run(
        repository.soft_delete_entry(
            owner_user_id=owner_user_id,
            entry_date=entry.entry_date,
            deleted_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        )
    )

    assert deleted is entry
    assert session.selects_for_update == 1
    assert session.refreshed == [(entry, ("updated_at",))]


def test_diary_repository_contains_concurrent_create_conflict_in_savepoint() -> None:
    owner_user_id = uuid4()
    session = FakeDiarySession(entry=None, flush_error=IntegrityError("INSERT", {}, Exception("duplicate owner/date")))
    repository = DiaryRepository(session=session)  # type: ignore[arg-type]

    entry = asyncio.run(
        repository.create_entry(
            owner_user_id=owner_user_id,
            entry_date=date(2026, 7, 2),
            values={"content": "Concurrent entry"},
        )
    )

    assert entry is None
    assert session.nested_transactions == 1


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
        self.update_content_mode = None

    async def get_entry_by_date(self, *, owner_user_id: UUID, entry_date: date):
        return self.entry

    async def list_entries(self, **kwargs):
        return self.entries

    async def create_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict):
        if self.entry is not None and self.entry.deleted_at is None:
            return None
        entry = self.entry or _entry(owner_user_id=owner_user_id)
        entry.entry_date = entry_date
        for field, value in values.items():
            setattr(entry, field, value)
        self.entry = entry
        return entry

    async def update_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict, content_mode: str = "replace"):
        if self.entry is None or self.entry.deleted_at is not None:
            return None
        self.update_content_mode = content_mode
        resolved_values = dict(values)
        if content_mode == "append" and "content" in resolved_values:
            current = str(self.entry.content or "").rstrip()
            addition = str(resolved_values["content"] or "").strip()
            resolved_values["content"] = current if not addition or current.endswith(f"\n{addition}") else f"{current}\n{addition}"
        for field, value in resolved_values.items():
            setattr(self.entry, field, value)
        return self.entry

    async def update_entry_with_status(self, **kwargs):
        before = {field: getattr(self.entry, field) for field in kwargs["values"]} if self.entry is not None else {}
        entry = await self.update_entry(**kwargs)
        changed = entry is not None and any(getattr(entry, field) != value for field, value in before.items())
        return DiaryEntryMutation(entry=entry, changed=changed) if entry is not None else None

    async def soft_delete_entry(self, **kwargs):
        if self.entry is None:
            return None
        self.entry.status = "deleted"
        self.entry.deleted_at = kwargs["deleted_at"]
        return self.entry


class FakeDiarySession:
    def __init__(self, *, entry: PregnancyDiaryEntry | None, flush_error: Exception | None = None) -> None:
        self.entry = entry
        self.flush_error = flush_error
        self.added = []
        self.flushed = False
        self.nested_transactions = 0
        self.refreshed = []
        self.selects_for_update = 0

    async def scalar(self, statement):
        if self.entry is None:
            return None
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" in sql:
            self.selects_for_update += 1
        if "deleted_at IS NULL" in sql and self.entry.deleted_at is not None:
            return None
        return self.entry

    def add(self, instance) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    async def refresh(self, instance, *, attribute_names) -> None:
        self.refreshed.append((instance, tuple(attribute_names)))

    def begin_nested(self):
        self.nested_transactions += 1
        return FakeNestedTransaction()


class FakeNestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
