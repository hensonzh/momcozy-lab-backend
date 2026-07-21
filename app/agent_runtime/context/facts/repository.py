from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserFact, UserFactExtractionRun


@dataclass(frozen=True)
class FactExtractionJobClaim:
    job_id: UUID
    lease_token: str
    stage: str
    attempts: int


class AgentFactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, *, owner_user_id: UUID, now: datetime | None = None) -> list[UserFact]:
        resolved_now = now or datetime.now(timezone.utc)
        statement = (
            select(UserFact)
            .where(
                UserFact.owner_user_id == owner_user_id,
                UserFact.status == "active",
                or_(UserFact.expires_at.is_(None), UserFact.expires_at > resolved_now),
            )
            .order_by(UserFact.fact_key, UserFact.fact_kind)
        )
        return list((await self.session.scalars(statement)).all())

    async def list_for_owner(
        self,
        *,
        owner_user_id: UUID,
        fact_kind: str | None,
        limit: int,
        now: datetime | None = None,
    ) -> list[UserFact]:
        resolved_now = now or datetime.now(timezone.utc)
        filters = [
            UserFact.owner_user_id == owner_user_id,
            UserFact.status == "active",
            or_(UserFact.expires_at.is_(None), UserFact.expires_at > resolved_now),
        ]
        if fact_kind is not None:
            filters.append(UserFact.fact_kind == fact_kind)
        statement = select(UserFact).where(*filters).order_by(UserFact.updated_at.desc(), UserFact.id.desc()).limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def get_for_owner(self, *, owner_user_id: UUID, fact_id: UUID) -> UserFact | None:
        statement = select(UserFact).where(
            UserFact.id == fact_id,
            UserFact.owner_user_id == owner_user_id,
        )
        return cast(UserFact | None, await self.session.scalar(statement))

    async def apply_fact(
        self,
        *,
        owner_user_id: UUID,
        fact_key: str,
        fact_kind: str,
        value: Any,
        source_type: str,
        source_id: str,
        sensitivity: str,
        observed_at: datetime,
        expires_at: datetime | None,
        catalog_version: str,
    ) -> tuple[UserFact, bool]:
        values = {
            "id": uuid4(),
            "owner_user_id": owner_user_id,
            "fact_key": fact_key,
            "fact_kind": fact_kind,
            "status": "active",
            "value": value,
            "source_type": source_type,
            "source_id": source_id[:255],
            "sensitivity": sensitivity,
            "catalog_version": catalog_version,
            "observed_at": observed_at,
            "expires_at": expires_at,
            "deleted_at": None,
            "deletion_reason": "",
            "version": 1,
        }
        insert_statement = postgresql_insert(UserFact).values(**values)
        incoming_is_newer = or_(
            and_(UserFact.status == "active", insert_statement.excluded.observed_at > UserFact.observed_at),
            and_(
                UserFact.status == "tombstoned",
                insert_statement.excluded.observed_at
                > func.coalesce(UserFact.deleted_at, UserFact.observed_at),
            ),
        )
        statement = (
            insert_statement.on_conflict_do_update(
                index_elements=[UserFact.owner_user_id, UserFact.fact_key, UserFact.fact_kind],
                set_={
                    "status": "active",
                    "value_json": insert_statement.excluded.value_json,
                    "source_type": insert_statement.excluded.source_type,
                    "source_id": insert_statement.excluded.source_id,
                    "sensitivity": insert_statement.excluded.sensitivity,
                    "catalog_version": insert_statement.excluded.catalog_version,
                    "observed_at": insert_statement.excluded.observed_at,
                    "expires_at": insert_statement.excluded.expires_at,
                    "deleted_at": None,
                    "deletion_reason": "",
                    "version": UserFact.version + 1,
                    "updated_at": func.now(),
                },
                where=incoming_is_newer,
            )
            .returning(UserFact.id)
        )
        fact_id = await self.session.scalar(statement)
        applied = fact_id is not None
        if fact_id is None:
            existing: UserFact | None = await self.session.scalar(
                select(UserFact).where(
                    UserFact.owner_user_id == owner_user_id,
                    UserFact.fact_key == fact_key,
                    UserFact.fact_kind == fact_kind,
                )
            )
            if existing is None:
                raise RuntimeError("Fact upsert did not return or resolve a row.")
            return existing, False
        fact = await self.session.get(UserFact, fact_id)
        if fact is None:
            raise RuntimeError("Fact upsert row could not be reloaded.")
        return fact, applied

    async def tombstone_for_owner(
        self,
        *,
        owner_user_id: UUID,
        fact_id: UUID,
        deleted_at: datetime,
        deletion_reason: str,
    ) -> UserFact | None:
        statement = (
            select(UserFact)
            .where(UserFact.id == fact_id, UserFact.owner_user_id == owner_user_id)
            .with_for_update()
        )
        fact = cast(UserFact | None, await self.session.scalar(statement))
        if fact is None:
            return None
        fact.status = "tombstoned"
        fact.value = None
        fact.source_id = ""
        fact.deleted_at = deleted_at
        fact.expires_at = None
        fact.deletion_reason = deletion_reason[:32]
        fact.version += 1
        await self.session.flush()
        return fact

    async def tombstone_kind_for_owner_key(
        self,
        *,
        owner_user_id: UUID,
        fact_key: str,
        fact_kind: str,
        deleted_at: datetime,
        deletion_reason: str,
    ) -> UserFact | None:
        statement = (
            select(UserFact)
            .where(
                UserFact.owner_user_id == owner_user_id,
                UserFact.fact_key == fact_key,
                UserFact.fact_kind == fact_kind,
                UserFact.status == "active",
            )
            .with_for_update()
        )
        fact = cast(UserFact | None, await self.session.scalar(statement))
        if fact is None:
            return None
        fact.status = "tombstoned"
        fact.value = None
        fact.source_id = ""
        fact.deleted_at = deleted_at
        fact.expires_at = None
        fact.deletion_reason = deletion_reason[:32]
        fact.version += 1
        await self.session.flush()
        return fact

    async def tombstone_all_kinds_for_owner_key(
        self,
        *,
        owner_user_id: UUID,
        fact_key: str,
        deleted_at: datetime,
        deletion_reason: str,
    ) -> list[UserFact]:
        statement = (
            select(UserFact)
            .where(
                UserFact.owner_user_id == owner_user_id,
                UserFact.fact_key == fact_key,
                UserFact.status == "active",
            )
            .with_for_update()
        )
        facts = list((await self.session.scalars(statement)).all())
        for fact in facts:
            fact.status = "tombstoned"
            fact.value = None
            fact.source_id = ""
            fact.deleted_at = deleted_at
            fact.expires_at = None
            fact.deletion_reason = deletion_reason[:32]
            fact.version += 1
        await self.session.flush()
        return facts

    async def tombstone_all_for_owner(
        self,
        *,
        owner_user_id: UUID,
        deleted_at: datetime,
        deletion_reason: str,
    ) -> list[UserFact]:
        statement = (
            select(UserFact)
            .where(UserFact.owner_user_id == owner_user_id, UserFact.status == "active")
            .with_for_update()
        )
        facts = list((await self.session.scalars(statement)).all())
        for fact in facts:
            fact.status = "tombstoned"
            fact.value = None
            fact.source_id = ""
            fact.deleted_at = deleted_at
            fact.expires_at = None
            fact.deletion_reason = deletion_reason[:32]
            fact.version += 1
        await self.session.flush()
        return facts

    async def expire_due_candidates(self, *, now: datetime, limit: int = 100) -> list[UserFact]:
        statement = (
            select(UserFact)
            .where(
                UserFact.fact_kind == "conversation_candidate",
                UserFact.status == "active",
                UserFact.expires_at.is_not(None),
                UserFact.expires_at <= now,
            )
            .order_by(UserFact.expires_at, UserFact.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        facts = list((await self.session.scalars(statement)).all())
        for fact in facts:
            fact.status = "tombstoned"
            fact.value = None
            fact.source_id = ""
            fact.deleted_at = now
            fact.expires_at = None
            fact.deletion_reason = "expired"
            fact.version += 1
        await self.session.flush()
        return facts

    async def enqueue_extraction(
        self,
        *,
        owner_user_id: UUID,
        source_message_id: UUID,
        source_run_id: UUID,
        catalog_version: str,
        extractor_version: str,
        model: str,
        max_attempts: int,
        request_id: str,
        trace_id: str,
        next_attempt_at: datetime,
    ) -> UserFactExtractionRun:
        statement = (
            postgresql_insert(UserFactExtractionRun)
            .values(
                owner_user_id=owner_user_id,
                source_message_id=source_message_id,
                source_run_id=source_run_id,
                catalog_version=catalog_version,
                extractor_version=extractor_version,
                model=model,
                max_attempts=max(1, max_attempts),
                request_id=request_id[:80],
                trace_id=trace_id[:120],
                next_attempt_at=next_attempt_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserFactExtractionRun.owner_user_id,
                    UserFactExtractionRun.source_message_id,
                    UserFactExtractionRun.catalog_version,
                    UserFactExtractionRun.extractor_version,
                ]
            )
            .returning(UserFactExtractionRun.id)
        )
        job_id = await self.session.scalar(statement)
        if job_id is None:
            existing: UserFactExtractionRun | None = await self.session.scalar(
                select(UserFactExtractionRun).where(
                    UserFactExtractionRun.owner_user_id == owner_user_id,
                    UserFactExtractionRun.source_message_id == source_message_id,
                    UserFactExtractionRun.catalog_version == catalog_version,
                    UserFactExtractionRun.extractor_version == extractor_version,
                )
            )
            if existing is None:
                raise RuntimeError("Fact extraction enqueue did not return or resolve a job.")
            return existing
        job: UserFactExtractionRun | None = await self.session.get(UserFactExtractionRun, job_id)
        if job is None:
            raise RuntimeError("Fact extraction job could not be reloaded.")
        return job

    async def claim_due_extractions(
        self,
        *,
        now: datetime,
        locked_until: datetime,
        limit: int,
    ) -> list[FactExtractionJobClaim]:
        statement = (
            select(UserFactExtractionRun)
            .where(
                or_(
                    and_(
                        UserFactExtractionRun.status.in_(("queued", "ready_to_apply")),
                        UserFactExtractionRun.next_attempt_at <= now,
                    ),
                    and_(
                        UserFactExtractionRun.status == "locked",
                        UserFactExtractionRun.locked_until.is_not(None),
                        UserFactExtractionRun.locked_until <= now,
                    ),
                )
            )
            .order_by(UserFactExtractionRun.next_attempt_at, UserFactExtractionRun.created_at, UserFactExtractionRun.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        jobs = list((await self.session.scalars(statement)).all())
        claims: list[FactExtractionJobClaim] = []
        for job in jobs:
            recovering_expired_lease = job.status == "locked"
            counts_attempt = job.stage == "extract" or recovering_expired_lease
            if counts_attempt and job.attempts >= job.max_attempts:
                job.status = "dead_lettered"
                job.candidates = []
                job.locked_until = None
                job.lease_token = ""
                job.error_code = "lease_attempts_exhausted"
                job.completed_at = now
                continue
            lease_token = uuid4().hex
            if counts_attempt:
                job.attempts += 1
            job.status = "locked"
            job.locked_until = locked_until
            job.lease_token = lease_token
            job.started_at = now
            claims.append(
                FactExtractionJobClaim(
                    job_id=job.id,
                    lease_token=lease_token,
                    stage=job.stage,
                    attempts=job.attempts,
                )
            )
        await self.session.flush()
        return claims

    async def get_claimed_extraction(
        self,
        *,
        job_id: UUID,
        lease_token: str,
        for_update: bool = False,
    ) -> UserFactExtractionRun | None:
        statement = select(UserFactExtractionRun).where(
            UserFactExtractionRun.id == job_id,
            UserFactExtractionRun.status == "locked",
            UserFactExtractionRun.lease_token == lease_token,
            UserFactExtractionRun.locked_until.is_not(None),
            UserFactExtractionRun.locked_until > func.now(),
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(UserFactExtractionRun | None, await self.session.scalar(statement))

    async def mark_ready_to_apply(
        self,
        *,
        job: UserFactExtractionRun,
        candidates: list[dict[str, Any]],
        extracted_count: int,
        rejected_count: int,
        next_attempt_at: datetime,
    ) -> None:
        job.status = "ready_to_apply"
        job.stage = "apply"
        job.candidates = candidates
        job.extracted_count = extracted_count
        job.rejected_count = rejected_count
        job.next_attempt_at = next_attempt_at
        job.locked_until = None
        job.lease_token = ""
        job.error_code = ""
        await self.session.flush()

    async def defer_apply(
        self,
        *,
        job: UserFactExtractionRun,
        next_attempt_at: datetime,
    ) -> None:
        job.status = "ready_to_apply"
        job.next_attempt_at = next_attempt_at
        job.locked_until = None
        job.lease_token = ""
        await self.session.flush()

    async def mark_completed(
        self,
        *,
        job: UserFactExtractionRun,
        applied_count: int,
        rejected_count: int,
        completed_at: datetime,
    ) -> None:
        job.status = "completed"
        job.applied_count = applied_count
        job.rejected_count = rejected_count
        job.candidates = []
        job.locked_until = None
        job.lease_token = ""
        job.error_code = ""
        job.completed_at = completed_at
        await self.session.flush()

    async def mark_skipped_disabled(self, *, job: UserFactExtractionRun, completed_at: datetime) -> None:
        job.status = "skipped_disabled"
        job.candidates = []
        job.locked_until = None
        job.lease_token = ""
        job.error_code = "memory_disabled"
        job.completed_at = completed_at
        await self.session.flush()

    async def mark_retention_expired(self, *, job: UserFactExtractionRun, completed_at: datetime) -> None:
        job.status = "cancelled"
        job.candidates = []
        job.locked_until = None
        job.lease_token = ""
        job.error_code = "source_run_nonterminal_deadline"
        job.completed_at = completed_at
        await self.session.flush()

    async def cancel_pending_extractions_for_owner(
        self,
        *,
        owner_user_id: UUID,
        completed_at: datetime,
        reason: str,
    ) -> list[UserFactExtractionRun]:
        statement = (
            select(UserFactExtractionRun)
            .where(
                UserFactExtractionRun.owner_user_id == owner_user_id,
                UserFactExtractionRun.status.in_(("queued", "locked", "ready_to_apply")),
            )
            .with_for_update()
        )
        jobs = list((await self.session.scalars(statement)).all())
        for job in jobs:
            job.status = "cancelled"
            job.candidates = []
            job.locked_until = None
            job.lease_token = ""
            job.error_code = reason[:120]
            job.completed_at = completed_at
        await self.session.flush()
        return jobs

    async def reschedule_or_dead_letter(
        self,
        *,
        job: UserFactExtractionRun,
        next_attempt_at: datetime,
        error_code: str,
        completed_at: datetime,
    ) -> None:
        retry_stage = job.stage
        if retry_stage == "apply":
            job.attempts += 1
        if job.attempts >= job.max_attempts:
            job.status = "dead_lettered"
            job.candidates = []
            job.completed_at = completed_at
        else:
            job.status = "ready_to_apply" if retry_stage == "apply" else "queued"
            job.next_attempt_at = next_attempt_at
        if retry_stage == "extract":
            job.candidates = []
        job.locked_until = None
        job.lease_token = ""
        job.error_code = error_code[:120]
        await self.session.flush()


__all__ = ["AgentFactRepository", "FactExtractionJobClaim"]
