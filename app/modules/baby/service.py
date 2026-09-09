from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import BabyRecord
from .repository import BabyRecordRepository
from .schemas import BabyRecordBatchWrite, BabyRecordList, BabyRecordRead, BabyRecordUpdate, DatedObservation, NotedObservation, Observation, RecordKind, SleepObservation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def conflict() -> ApiError:
    return ApiError(code='version_conflict', message='This record changed. Reload before saving.', status=409)


class BabyRecordService:
    def __init__(self, repository: BabyRecordRepository, audit: AuditService, idempotency: IdempotencyService, *, now: Callable[[], datetime] = utc_now) -> None:
        self.repository, self.audit, self.idempotency, self.now = repository, audit, idempotency, now

    async def list(self, owner: UUID, baby_id: UUID, start_date: date, end_date: date, *, timezone_name: str, kind: RecordKind | None, offset: int, limit: int) -> BabyRecordList:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ApiError(code='validation_failed', message='Use an IANA timezone.', status=422) from error
        if end_date <= start_date or end_date - start_date > timedelta(days=367):
            raise ApiError(code='validation_failed', message='Choose a date range of at most 367 days.', status=422)
        start, end = datetime.combine(start_date, time.min, zone), datetime.combine(end_date, time.min, zone)
        await self.repository.baby(owner, baby_id)
        values, total = await self.repository.list(owner, baby_id, start, end, start_date, end_date, timezone_name, as_of=self.now(), kind=kind, offset=offset, limit=limit)
        return BabyRecordList(items=[BabyRecordRead.model_validate(value) for value in values], total=total, offset=offset, limit=limit, server_time=self.now())

    async def create(self, owner: UUID, baby_id: UUID, observation: Observation, key: str, request_id: str) -> BabyRecord:
        baby = await self.repository.baby(owner, baby_id, write=True)
        decision = await self.idempotency.reserve(actor_user_id=owner, scope=f'baby_record.create:{baby_id}', key=key,
            request_hash=request_hash(observation.model_dump(mode='json')), expires_at=self.now() + timedelta(days=1))
        if decision.status == 'replay':
            value = await self.repository.get(owner, baby_id, parse_idempotency_response_ref(decision.record.response_ref))
            if value.deleted_at is not None:
                raise ApiError(code='record_deleted', message='This record was deleted.', status=409)
            return value
        await self._validate(baby_id, observation, birth_date=baby.birth_date)
        value = BabyRecord(owner_user_id=owner, baby_id=baby_id, version=1, created_at=self.now(), updated_at=self.now())
        self._apply(value, observation)
        self.repository.session.add(value)
        await self.repository.session.flush()
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(value.id))
        await self._audit(owner, value, 'create', request_id)
        return value

    async def create_batch(self, owner: UUID, baby_id: UUID, body: BabyRecordBatchWrite, key: str, request_id: str) -> Sequence[BabyRecord]:
        baby = await self.repository.baby(owner, baby_id, write=True)
        decision = await self.idempotency.reserve(actor_user_id=owner, scope=f'baby_record.batch:{baby_id}', key=key,
            request_hash=request_hash(body.model_dump(mode='json')), expires_at=self.now() + timedelta(days=1))
        if decision.status == 'replay':
            values = [await self.repository.get(owner, baby_id, UUID(item)) for item in json.loads(decision.record.response_ref)]
            if any(value.deleted_at is not None for value in values):
                raise ApiError(code='record_deleted', message='A record from this entry was deleted.', status=409)
            return values
        for observation in body.observations:
            await self._validate(baby_id, observation, birth_date=baby.birth_date)
        values = []
        for observation in body.observations:
            value = BabyRecord(owner_user_id=owner, baby_id=baby_id, version=1, created_at=self.now(), updated_at=self.now())
            self._apply(value, observation)
            self.repository.session.add(value)
            values.append(value)
        await self.repository.session.flush()
        await self.idempotency.mark_completed(record=decision.record, response_ref=json.dumps([str(value.id) for value in values]))
        for value in values:
            await self._audit(owner, value, 'create', request_id)
        return values

    async def latest_growth(self, owner: UUID, baby_id: UUID) -> Sequence[BabyRecord]:
        await self.repository.baby(owner, baby_id)
        return await self.repository.latest_growth(owner, baby_id)

    async def update(self, owner: UUID, baby_id: UUID, record_id: UUID, body: BabyRecordUpdate, request_id: str) -> BabyRecord:
        baby = await self.repository.baby(owner, baby_id, write=True)
        value = await self.repository.get(owner, baby_id, record_id)
        if value.deleted_at is None and value.version == body.expected_version + 1 and value.observation == body.observation:
            return value
        if value.deleted_at is not None or value.version != body.expected_version:
            raise conflict()
        if value.kind != body.observation.kind:
            raise ApiError(code='record_kind_changed', message='Edit a record within its original category.', status=422)
        await self._validate(baby_id, body.observation, birth_date=baby.birth_date, exclude=record_id)
        self._apply(value, body.observation)
        value.version += 1
        value.updated_at = self.now()
        await self.repository.session.flush()
        await self._audit(owner, value, 'update', request_id)
        return value

    async def set_deleted(self, owner: UUID, baby_id: UUID, record_id: UUID, version: int, deleted: bool, request_id: str) -> BabyRecord:
        baby = await self.repository.baby(owner, baby_id, write=True)
        value = await self.repository.get(owner, baby_id, record_id)
        if (value.deleted_at is not None) == deleted and value.version == version + 1:
            return value
        if value.version != version or (value.deleted_at is not None) == deleted:
            raise conflict()
        if not deleted:
            await self._validate(baby_id, value.observation, birth_date=baby.birth_date, exclude=record_id)
        value.deleted_at, value.version, value.updated_at = self.now() if deleted else None, value.version + 1, self.now()
        await self.repository.session.flush()
        await self._audit(owner, value, 'delete' if deleted else 'restore', request_id)
        return value

    async def _validate(self, baby_id: UUID, observation: Observation, *, birth_date: date | None, exclude: UUID | None = None) -> None:
        if isinstance(observation, DatedObservation):
            if observation.recorded_on > self.now().astimezone(ZoneInfo(observation.timezone)).date() or (birth_date is not None and observation.recorded_on < birth_date):
                raise ApiError(code='validation_failed', message='Choose a date from birth through today in the recording timezone.', status=422)
        elif observation.occurred_at > self.now() or (isinstance(observation, SleepObservation) and observation.ended_at is not None and observation.ended_at > self.now()):
            raise ApiError(code='validation_failed', message='Record times cannot be in the future.', status=422)
        if isinstance(observation, SleepObservation) and observation.ended_at is None and await self.repository.active_sleep(baby_id, exclude):
            raise ApiError(code='active_sleep_exists', message='Finish the current sleep record before starting another.', status=409)

    @staticmethod
    def _apply(value: BabyRecord, observation: Observation) -> None:
        value.kind = observation.kind
        value.occurred_at = observation.occurred_at if isinstance(observation, NotedObservation) else None
        value.recorded_on = observation.recorded_on if isinstance(observation, DatedObservation) else None
        value.ended_at = observation.ended_at if isinstance(observation, SleepObservation) else None
        value.data = observation.model_dump(mode='json', exclude={'kind', 'occurred_at', 'recorded_on', 'ended_at', 'unit', 'label'})

    async def _audit(self, owner: UUID, value: BabyRecord, action: str, request_id: str) -> None:
        await self.audit.record(actor_user_id=owner, action=f'baby.record.{action}', resource_type='baby_record', resource_id=str(value.id),
            request_id=request_id, details={'baby_id': str(value.baby_id), 'kind': value.kind, 'version': value.version})
