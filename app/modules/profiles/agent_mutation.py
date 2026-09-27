"""Atomic, owner-scoped record and personal-schedule batches for Agent Runtime.

Conversational consent belongs to the Agent, not this service. This boundary
still validates business data, ownership, concurrency and replay safety.
"""
from __future__ import annotations

from datetime import date as CalendarDate, datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ...core.errors import ApiError
from ...infrastructure.db.base import Base
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, request_hash
from ..baby.models import BabyRecord
from ..baby.profile_models import BabyProfile
from ..users.models import AccountStatus, User
from ..baby.schemas import DatedObservation, NotedObservation, observation_adapter
from ..plans.models import PlanTask
from ..records.models import FeedingRecord, GrowthRecord, PumpingRecord
from ..records.schemas import FeedingRecordCreate, PumpingRecordCreate
from ..schedule.schemas import PersonalScheduleWrite
from .me_models import MotherObservation
from .me_schemas import Observation as MotherObservationWrite
from .repository import ProfileRepository
from .topical_records import Source, Topic


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordFields(Strict):
    occurred_at: AwareDatetime | None = None
    recorded_on: CalendarDate | None = None
    method: Literal["breastfeeding", "expressed_milk", "formula"] | None = None
    side: str | None = None
    volume_ml: float | None = Field(default=None, gt=0, le=3000)
    duration_minutes: int | None = Field(default=None, gt=0, le=240)
    diaper_kind: Literal["wet", "dirty", "both"] | None = None
    wet_count: int | None = Field(default=None, ge=1, le=100)
    stool_count: int | None = Field(default=None, ge=1, le=100)
    color: str | None = None
    consistency: str | None = None
    signs: list[Literal["blood", "mucus"]] | None = None
    metric: Literal["weight", "length", "head_circumference"] | None = None
    value: float | None = Field(default=None, gt=0, le=150)
    measurement_source: Literal["home", "clinic", "other"] | None = None
    mental_state: Literal["content", "active", "crying", "drowsy"] | None = None
    pain_score: int | None = Field(default=None, ge=0, le=10)
    phase: str | None = None
    impact: str | None = None
    latch_status: Literal["Stayed latched", "Came off easily", "Could not latch", "含得稳", "容易松开", "含不住"] | None = None
    weight_kg: float | None = Field(default=None, gt=0, le=50)
    height_cm: float | None = Field(default=None, gt=0, le=150)
    head_circumference_cm: float | None = Field(default=None, gt=0, le=150)


CREATE_FIELDS: dict[Topic, frozenset[str]] = {
    "feeding": frozenset({"occurred_at", "method", "side", "volume_ml", "duration_minutes"}),
    "pumping": frozenset({"occurred_at", "side", "volume_ml", "duration_minutes"}),
    "diaper": frozenset({"occurred_at", "recorded_on", "diaper_kind", "wet_count", "stool_count", "color", "consistency", "signs"}),
    "pain": frozenset({"occurred_at", "pain_score", "side", "phase", "impact"}),
    "latch": frozenset({"occurred_at", "latch_status"}),
    "growth": frozenset({"recorded_on", "metric", "value", "measurement_source"}),
    "after_feeding_mood": frozenset({"recorded_on", "mental_state"}),
}
LEGACY_FIELDS: dict[Source, frozenset[str]] = {
    "baby_records": frozenset(),
    "mother_observations": frozenset(),
    "feeding_records": frozenset({"occurred_at", "method", "volume_ml", "duration_minutes"}),
    "pumping_records": frozenset({"occurred_at", "side", "volume_ml", "duration_minutes"}),
    "growth_records": frozenset({"weight_kg", "height_cm", "head_circumference_cm"}),
}
SOURCE_TOPICS: dict[Source, frozenset[Topic]] = {
    "baby_records": frozenset({"feeding", "diaper", "growth", "after_feeding_mood"}),
    "mother_observations": frozenset({"pain", "latch"}),
    "feeding_records": frozenset({"feeding"}),
    "pumping_records": frozenset({"pumping"}),
    "growth_records": frozenset({"growth"}),
}
BABY_TOPICS = frozenset({"feeding", "diaper", "growth", "after_feeding_mood"})


class RecordOperation(Strict):
    op: Literal["create", "update"]
    topic: Topic
    infant_id: UUID | None = None
    record_type: Literal["event", "daily_summary"] | None = None
    record_source: Source | None = None
    record_id: UUID | None = None
    revision: str | None = Field(default=None, min_length=1, max_length=80)
    fields: RecordFields

    @model_validator(mode="after")
    def valid_target(self) -> RecordOperation:
        if (self.topic in BABY_TOPICS) != (self.infant_id is not None):
            raise ValueError("Baby topics require infant_id; maternal topics must omit it.")
        if self.topic == "diaper" and self.record_type is None:
            raise ValueError("Diaper requires event or daily_summary.")
        if self.topic != "diaper" and self.record_type is not None:
            raise ValueError("Only diaper has a record_type.")
        if self.op == "create" and any((self.record_source, self.record_id, self.revision)):
            raise ValueError("Creation cannot target an existing entry.")
        if self.op == "update" and (self.record_source is None or self.record_id is None or self.revision is None):
            raise ValueError("Update requires source, record ID and revision from a read.")
        if self.record_source is not None and self.topic not in SOURCE_TOPICS[self.record_source]:
            raise ValueError("Topic and record source do not match.")
        allowed = CREATE_FIELDS[self.topic]
        if self.op == "update" and self.record_source not in {"baby_records", "mother_observations"}:
            assert self.record_source is not None
            allowed = LEGACY_FIELDS[self.record_source]
        fields = self.fields.model_dump(exclude_unset=True)
        if not fields or fields.keys() - allowed:
            raise ValueError("Record fields are empty or not supported by this source.")
        if self.topic == "diaper":
            valid = ({"occurred_at", "diaper_kind", "color", "consistency", "signs"}
                     if self.record_type == "event" else {"recorded_on", "wet_count", "stool_count", "color", "consistency"})
            if fields.keys() - valid:
                raise ValueError("Diaper fields disagree with record_type.")
        return self


class RecordBatch(Strict):
    actor_user_id: UUID
    timezone: str
    operations: list[RecordOperation] = Field(min_length=1, max_length=20)


class ScheduleFields(Strict):
    title: str | None = None
    date: CalendarDate | None = None
    start_time: str | None = None
    note: str | None = None


class ScheduleOperation(Strict):
    op: Literal["create", "update"]
    task_id: UUID | None = None
    expected_updated_at: AwareDatetime | None = None
    fields: ScheduleFields

    @model_validator(mode="after")
    def valid_target(self) -> ScheduleOperation:
        if self.op == "create" and (self.task_id is not None or self.expected_updated_at is not None):
            raise ValueError("Creation cannot target an existing event.")
        if self.op == "update" and (self.task_id is None or self.expected_updated_at is None):
            raise ValueError("Update requires ID and revision.")
        if not self.fields.model_fields_set:
            raise ValueError("Changes cannot be empty.")
        return self


class ScheduleBatch(Strict):
    actor_user_id: UUID
    operations: list[ScheduleOperation] = Field(min_length=1, max_length=20)


class BatchItem(Strict):
    op: Literal["create", "update"]
    resource_id: UUID
    revision: str


class BatchResult(Strict):
    batch_id: UUID
    items: list[BatchItem]


class AgentBatchReceipt(Base):
    __tablename__ = "agent_batch_receipts"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


def _invalid(index: int, message: str = "Record fields are invalid.", *, field: str = "", reason: str = "invalid_fields") -> ApiError:
    return ApiError(code="validation_failed", message=f"Item {index}: {message}", status=422,
        details={"operation_index": index, "field_path": field, "reason": reason})


def _conflict(index: int, *, field: str = "revision") -> ApiError:
    return ApiError(code="version_conflict", message=f"Item {index} changed. Read it again before updating.", status=409,
        details={"operation_index": index, "field_path": field, "reason": "stale_revision"})


def _not_found(index: int) -> ApiError:
    return ApiError(code="not_found", message=f"Item {index} was not found.", status=404,
        details={"operation_index": index, "field_path": "", "reason": "not_found"})


def _revision(value: datetime) -> str:
    return value.isoformat() if value.tzinfo else value.replace(tzinfo=timezone.utc).isoformat()


class AgentBatchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.profiles = ProfileRepository(session)
        audit = AuditRepository(session)
        self.audit = AuditService(repository=audit)

    async def _replayed(self, *, owner: UUID, key: str, scope: str, payload: dict[str, Any]) -> tuple[UUID, BatchResult | None, str]:
        try:
            batch_id = UUID(key)
        except ValueError as exc:
            raise ApiError(code="validation_failed", message="Action identity is invalid.", status=422) from exc
        digest = request_hash({"scope": scope, "payload": payload})
        receipt = await self.session.get(AgentBatchReceipt, batch_id)
        if receipt is None:
            return batch_id, None, digest
        if receipt.owner_user_id != owner or receipt.request_hash != digest:
            raise ApiError(code="idempotency_conflict", message="Action identity has different contents.", status=409)
        return batch_id, BatchResult.model_validate(receipt.result), digest

    async def _finish(self, *, owner: UUID, batch_id: UUID, digest: str, result: BatchResult) -> None:
        self.session.add(AgentBatchReceipt(id=batch_id, owner_user_id=owner, request_hash=digest,
            result=result.model_dump(mode="json")))
        await self.session.flush()

    async def _lock_owner(self, owner: UUID) -> None:
        active = await self.session.scalar(select(User.id).where(
            User.id == owner, User.status == AccountStatus.ACTIVE, User.deleted_at.is_(None),
        ).with_for_update())
        if active is None:
            raise ApiError(code="account_inactive", message="Account is not active.", status=403)

    async def records(self, batch: RecordBatch, *, key: str, request_id: str) -> BatchResult:
        try:
            zone = ZoneInfo(batch.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise _invalid(0, "Use an IANA timezone.") from exc
        owner = batch.actor_user_id
        await self._lock_owner(owner)
        batch_id, replay, digest = await self._replayed(owner=owner, key=key, scope="agent.records.batch", payload=batch.model_dump(mode="json", exclude_unset=True))
        if replay is not None:
            return replay
        results: list[BatchItem] = []
        seen: dict[tuple[Source, UUID], str] = {}
        updated: set[tuple[Source, UUID]] = set()
        for index, operation in enumerate(batch.operations):
            if operation.op == "update":
                assert operation.record_source is not None and operation.record_id is not None and operation.revision is not None
                identity = (operation.record_source, operation.record_id)
                previous = seen.setdefault(identity, operation.revision)
                if previous != operation.revision:
                    raise _conflict(index)
            if operation.infant_id is not None and not await self.profiles.is_current_delivery_infant(owner_user_id=owner, infant_id=operation.infant_id):
                raise _not_found(index)
            results.append(await self._record(owner, operation, zone, index, updated))
        result = BatchResult(batch_id=batch_id, items=results)
        await self._finish(owner=owner, batch_id=batch_id, digest=digest, result=result)
        await self.audit.record(actor_user_id=owner, action="agent.records.batch", resource_type="record_batch",
            resource_id=str(result.batch_id), request_id=request_id, details={"count": len(results)})
        return result

    async def schedule(self, batch: ScheduleBatch, *, key: str, request_id: str) -> BatchResult:
        owner = batch.actor_user_id
        await self._lock_owner(owner)
        batch_id, replay, digest = await self._replayed(owner=owner, key=key, scope="agent.schedule.batch", payload=batch.model_dump(mode="json", exclude_unset=True))
        if replay is not None:
            return replay
        results: list[BatchItem] = []
        seen_schedule: dict[UUID, str] = {}
        for index, operation in enumerate(batch.operations):
            task = None
            if operation.op == "update":
                task = await self.session.scalar(select(PlanTask).where(
                    PlanTask.owner_user_id == owner, PlanTask.id == operation.task_id, PlanTask.deleted_at.is_(None),
                    PlanTask.plan_id.is_(None), PlanTask.payload["schedule_kind"].astext == "personal",
                ).with_for_update())
                if task is None:
                    raise _not_found(index)
                assert operation.task_id is not None and operation.expected_updated_at is not None
                original_revision = _revision(operation.expected_updated_at)
                if seen_schedule.setdefault(operation.task_id, original_revision) != original_revision:
                    raise _conflict(index, field="expected_updated_at")
                if _revision(task.updated_at) != original_revision and not any(
                    prior.resource_id == operation.task_id for prior in results
                ):
                    raise _conflict(index, field="expected_updated_at")
            fields = operation.fields.model_dump(exclude_unset=True)
            if task is not None:
                fields = {"title": task.title, "date": task.task_date, "start_time": task.task_time, "note": task.description, **fields}
            try:
                entry = PersonalScheduleWrite.model_validate(fields)
            except ValidationError as exc:
                first = exc.errors(include_url=False)[0]
                field = first["loc"][0] if first["loc"] else ""
                safe_field = str(field) if field in {"title", "date", "start_time", "note"} else ""
                raise _invalid(index, "Schedule fields are invalid.", field=f"fields.{safe_field}" if safe_field else "",
                    reason="required" if first["type"] == "missing" else "invalid_value") from exc
            if task is None:
                task = PlanTask(owner_user_id=owner, task_date=entry.date, task_time=entry.start_time,
                    title=entry.title, description=entry.note, payload={"schedule_kind": "personal"})
                self.session.add(task)
            else:
                task.title, task.task_date, task.task_time, task.description = entry.title, entry.date, entry.start_time, entry.note
                task.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            results.append(BatchItem(op=operation.op, resource_id=task.id, revision=_revision(task.updated_at)))
        result = BatchResult(batch_id=batch_id, items=results)
        await self._finish(owner=owner, batch_id=batch_id, digest=digest, result=result)
        await self.audit.record(actor_user_id=owner, action="agent.schedule.batch", resource_type="schedule_batch",
            resource_id=str(result.batch_id), request_id=request_id, details={"count": len(results)})
        return result

    async def _record(self, owner: UUID, op: RecordOperation, zone: ZoneInfo, index: int,
                      updated: set[tuple[Source, UUID]]) -> BatchItem:
        if op.topic in {"feeding", "diaper", "growth", "after_feeding_mood"} and (op.op == "create" or op.record_source == "baby_records"):
            return await self._baby(owner, op, zone, index, updated)
        if op.topic in {"pain", "latch"}:
            return await self._mother(owner, op, index, updated)
        if op.topic == "pumping" and (op.op == "create" or op.record_source == "pumping_records"):
            return await self._pumping(owner, op, index, updated)
        if op.topic == "feeding" and op.record_source == "feeding_records":
            return await self._legacy_feeding(owner, op, index, updated)
        if op.topic == "growth" and op.record_source == "growth_records":
            return await self._legacy_growth(owner, op, index, updated)
        raise _invalid(index, "Record source is not writable.")

    async def _baby(self, owner: UUID, op: RecordOperation, zone: ZoneInfo, index: int, updated: set[tuple[Source, UUID]]) -> BatchItem:
        assert op.infant_id is not None
        baby = await self.session.scalar(select(BabyProfile).where(BabyProfile.id == op.infant_id,
            BabyProfile.owner_user_id == owner, BabyProfile.deleted_at.is_(None)))
        if baby is None:
            raise _not_found(index)
        record = None
        if op.op == "update":
            record = await self.session.scalar(select(BabyRecord).where(
                BabyRecord.id == op.record_id, BabyRecord.owner_user_id == owner,
                BabyRecord.baby_id == op.infant_id, BabyRecord.deleted_at.is_(None),
            ).with_for_update())
            kind = "daily_status" if op.topic == "after_feeding_mood" or op.record_type == "daily_summary" else op.topic
            if record is None or record.kind != kind:
                raise _not_found(index)
            assert op.record_id is not None
            if ("baby_records", op.record_id) not in updated and str(record.version) != op.revision:
                raise _conflict(index)
            values = {"kind": record.kind, **record.data}
            values["recorded_on" if record.recorded_on else "occurred_at"] = record.recorded_on or record.occurred_at
        else:
            values = {"kind": "daily_status" if op.topic == "after_feeding_mood" or op.record_type == "daily_summary" else op.topic}
        fields = op.fields.model_dump(exclude_unset=True)
        if op.topic == "after_feeding_mood" and values["kind"] != "daily_status":
            raise _not_found(index)
        if op.topic == "diaper" and op.record_type == "daily_summary" and values["kind"] != "daily_status":
            raise _not_found(index)
        if op.topic == "diaper" and op.record_type == "event" and values["kind"] != "diaper":
            raise _not_found(index)
        if values["kind"] in {"daily_status", "growth"}:
            values.setdefault("timezone", record.data.get("timezone", zone.key) if record else zone.key)
        values.update(fields)
        try:
            observation = observation_adapter.validate_python(values)
        except ValidationError as exc:
            raise _invalid(index) from exc
        if isinstance(observation, DatedObservation):
            if observation.recorded_on > datetime.now(zone).date() or (baby.birth_date and observation.recorded_on < baby.birth_date):
                raise _invalid(index, "Date must be from birth through today.")
        elif isinstance(observation, NotedObservation) and (observation.occurred_at > datetime.now(timezone.utc)
                or (baby.birth_date and observation.occurred_at.astimezone(zone).date() < baby.birth_date)):
            raise _invalid(index, "Time must be from birth through now.")
        if record is None:
            record = BabyRecord(owner_user_id=owner, baby_id=op.infant_id, version=1)
            self.session.add(record)
        else:
            assert op.record_id is not None
            if ("baby_records", op.record_id) not in updated:
                record.version += 1
                updated.add(("baby_records", op.record_id))
        record.kind = observation.kind
        record.occurred_at = getattr(observation, "occurred_at", None)
        record.recorded_on = getattr(observation, "recorded_on", None)
        record.data = observation.model_dump(mode="json", exclude={"kind", "occurred_at", "recorded_on", "ended_at", "unit", "label"})
        record.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return BatchItem(op=op.op, resource_id=record.id, revision=str(record.version))

    async def _mother(self, owner: UUID, op: RecordOperation, index: int, updated: set[tuple[Source, UUID]]) -> BatchItem:
        record = None
        if op.op == "update":
            record = await self.session.scalar(select(MotherObservation).where(
                MotherObservation.owner_user_id == owner, MotherObservation.id == op.record_id, MotherObservation.kind == op.topic,
            ).with_for_update())
            if record is None:
                raise _not_found(index)
            assert op.record_id is not None
            if ("mother_observations", op.record_id) not in updated and _revision(record.updated_at) != op.revision:
                raise _conflict(index)
        fields = op.fields.model_dump(exclude_unset=True)
        occurred_at = fields.pop("occurred_at", record.occurred_at if record else None)
        if occurred_at is None:
            raise _invalid(index, "Observation time is required.")
        values: dict[str, Any] = {"id": record.id if record else uuid4(), "kind": op.topic,
            "occurred_at": occurred_at, "value": record.value if record else "",
            "fields": dict(record.fields) if record else {}}
        if op.topic == "latch":
            values["value"] = fields.pop("latch_status", values["value"])
        else:
            if "pain_score" in fields:
                values["fields"]["pain"] = fields.pop("pain_score")
                values["value"] = f"{values['fields']['pain']} / 10"
            values["fields"].update(fields)
        try:
            entry = MotherObservationWrite.model_validate(values)
        except ValidationError as exc:
            raise _invalid(index) from exc
        if record is None:
            record = MotherObservation(owner_user_id=owner, id=entry.id)
            self.session.add(record)
        record.kind, record.occurred_at, record.value = entry.kind, entry.occurred_at, entry.value
        if op.op == "update":
            assert op.record_id is not None
            updated.add(("mother_observations", op.record_id))
        record.fields = entry.fields.model_dump(mode="json", exclude_none=True)
        record.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return BatchItem(op=op.op, resource_id=record.id, revision=_revision(record.updated_at))

    async def _pumping(self, owner: UUID, op: RecordOperation, index: int, updated: set[tuple[Source, UUID]]) -> BatchItem:
        fields = op.fields.model_dump(exclude_unset=True)
        allowed_sides = {"Left side", "Right side", "Both sides", "左侧", "右侧", "两侧"}
        if op.op == "create" and fields.get("volume_ml") is None:
            raise _invalid(index, "New pumping entries need measured volume.", field="fields.volume_ml", reason="required")
        if op.op == "create" and fields.get("side") is None:
            raise _invalid(index, "New pumping entries need a side.", field="fields.side", reason="required")
        if "side" in fields and fields["side"] not in allowed_sides:
            raise _invalid(index, "Pumping side is invalid.", field="fields.side", reason="invalid_value")
        record = None
        if op.op == "update":
            record = await self.session.scalar(select(PumpingRecord).where(
                PumpingRecord.owner_user_id == owner, PumpingRecord.id == op.record_id,
                PumpingRecord.status == "active", PumpingRecord.deleted_at.is_(None),
            ).with_for_update())
            if record is None:
                raise _not_found(index)
            assert op.record_id is not None
            if ("pumping_records", op.record_id) not in updated and _revision(record.updated_at) != op.revision:
                raise _conflict(index)
        values: dict[str, Any] = {"pump_start_time": fields.get("occurred_at", record.pump_start_time if record else None),
            "milk_volume_ml": fields.get("volume_ml", record.milk_volume_ml if record else None),
            "duration_seconds": (fields["duration_minutes"] * 60 if fields.get("duration_minutes") is not None else record.duration_seconds if record else None)}
        try:
            body = PumpingRecordCreate.model_validate(values)
        except ValidationError as exc:
            raise _invalid(index) from exc
        if body.pump_start_time.tzinfo is None or body.pump_start_time > datetime.now(timezone.utc):
            raise _invalid(index, "Pumping time cannot be in the future.", field="fields.occurred_at", reason="future_time")
        # A Me observation requires both measured volume and side. Keep the
        # canonical event and its App mirror valid in the same transaction.
        mirrors: list[MotherObservation] = []
        if record is not None:
            mirrors = list(await self.session.scalars(select(MotherObservation).where(
                MotherObservation.owner_user_id == owner, MotherObservation.kind == "pump",
                MotherObservation.fields["canonical_record_id"].astext == str(record.id),
            ).with_for_update()))
        if (mirrors or "side" in fields) and body.milk_volume_ml is None:
            raise _invalid(index, "An App pumping entry requires measured volume.", field="fields.volume_ml", reason="required")
        if record is None:
            record = PumpingRecord(owner_user_id=owner, pump_start_time=body.pump_start_time, milk_volume_ml=body.milk_volume_ml,
                duration_seconds=body.duration_seconds, source="manual", pump_type="manual")
            self.session.add(record)
        else:
            record.pump_start_time, record.milk_volume_ml, record.duration_seconds = body.pump_start_time, body.milk_volume_ml, body.duration_seconds
            record.updated_at = datetime.now(timezone.utc)
            assert op.record_id is not None
            updated.add(("pumping_records", op.record_id))
        await self.session.flush()
        # The Me page stores a linked observation as well as the canonical event.
        minutes = record.duration_seconds / 60 if record.duration_seconds is not None else None
        display = f"{record.milk_volume_ml} ml" if record.milk_volume_ml is not None else f"{minutes} min"
        for mirror in mirrors:
            mirror.occurred_at, mirror.value = record.pump_start_time, display
            mirror.fields = {**mirror.fields, "volume_ml": record.milk_volume_ml,
                "duration_minutes": minutes, "side": fields.get("side", mirror.fields.get("side"))}
        if op.op == "create" or ("side" in fields and not mirrors):
            self.session.add(MotherObservation(owner_user_id=owner, id=uuid4(), kind="pump", occurred_at=record.pump_start_time,
                value=display, fields={"canonical_record_id": str(record.id), "volume_ml": record.milk_volume_ml,
                    "duration_minutes": minutes, "side": fields.get("side")}))
        await self.session.flush()
        return BatchItem(op=op.op, resource_id=record.id, revision=_revision(record.updated_at))

    async def _legacy_feeding(self, owner: UUID, op: RecordOperation, index: int, updated: set[tuple[Source, UUID]]) -> BatchItem:
        record = await self.session.scalar(select(FeedingRecord).where(FeedingRecord.owner_user_id == owner,
            FeedingRecord.id == op.record_id, FeedingRecord.infant_id == op.infant_id,
            FeedingRecord.status == "active", FeedingRecord.deleted_at.is_(None)).with_for_update())
        if record is None:
            raise _not_found(index)
        assert op.record_id is not None
        if ("feeding_records", op.record_id) not in updated and _revision(record.updated_at) != op.revision:
            raise _conflict(index)
        fields = op.fields.model_dump(exclude_unset=True)
        values = {"feed_time": fields.get("occurred_at", record.feed_time), "feed_type": fields.get("method", record.feed_type),
            "volume_ml": fields.get("volume_ml", record.volume_ml),
            "duration_seconds": fields["duration_minutes"] * 60 if fields.get("duration_minutes") is not None else record.duration_seconds}
        try:
            entry = FeedingRecordCreate.model_validate(values)
        except ValidationError as exc:
            raise _invalid(index) from exc
        if entry.feed_time.tzinfo is None or entry.feed_time > datetime.now(timezone.utc):
            raise _invalid(index, "Feeding time cannot be in the future.")
        record.feed_time, record.feed_type, record.volume_ml, record.duration_seconds = entry.feed_time, entry.feed_type, entry.volume_ml, entry.duration_seconds
        assert op.record_id is not None
        updated.add(("feeding_records", op.record_id))
        record.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return BatchItem(op=op.op, resource_id=record.id, revision=_revision(record.updated_at))

    async def _legacy_growth(self, owner: UUID, op: RecordOperation, index: int, updated: set[tuple[Source, UUID]]) -> BatchItem:
        record = await self.session.scalar(select(GrowthRecord).where(GrowthRecord.owner_user_id == owner,
            GrowthRecord.id == op.record_id, GrowthRecord.infant_id == op.infant_id,
            GrowthRecord.status == "active", GrowthRecord.deleted_at.is_(None)).with_for_update())
        if record is None:
            raise _not_found(index)
        assert op.record_id is not None
        if ("growth_records", op.record_id) not in updated and _revision(record.updated_at) != op.revision:
            raise _conflict(index)
        fields = op.fields.model_dump(exclude_unset=True)
        record.weight_kg = fields.get("weight_kg", record.weight_kg)
        record.height_cm = fields.get("height_cm", record.height_cm)
        record.head_cm = fields.get("head_circumference_cm", record.head_cm)
        assert op.record_id is not None
        updated.add(("growth_records", op.record_id))
        if all(v is None for v in (record.weight_kg, record.height_cm, record.head_cm)):
            raise _invalid(index, "At least one measurement is needed.")
        record.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return BatchItem(op=op.op, resource_id=record.id, revision=_revision(record.updated_at))
