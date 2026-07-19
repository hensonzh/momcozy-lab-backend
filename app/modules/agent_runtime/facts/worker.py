from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...audit import AuditService
from ...audit.repository import AuditRepository
from ..memory.service import AgentMemoryRepository, AgentMemoryService
from ..repository import AgentRuntimeRepository
from .extraction import AgentFactExtractor, FactExtractionContext, fact_inputs_to_candidate_payload
from .repository import AgentFactRepository, FactExtractionJobClaim
from .service import AgentFactService


TERMINAL_SOURCE_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
PENDING_CANDIDATE_RETENTION = timedelta(days=14)


@dataclass(frozen=True)
class FactExtractionProcessResult:
    status: str
    applied_count: int = 0


@dataclass(frozen=True)
class PreparedFactExtraction:
    owner_user_id: UUID
    run_id: UUID
    message_id: UUID
    source_text: str
    recent_dialogue: tuple[tuple[str, str], ...]
    observed_at: datetime
    request_id: str
    trace_id: str


class AgentFactExtractionWorker:
    def __init__(
        self,
        *,
        session_factory: Any,
        extractor: AgentFactExtractor,
        retry_base_seconds: float = 1.0,
        apply_poll_seconds: float = 0.25,
    ) -> None:
        self.session_factory = session_factory
        self.extractor = extractor
        self.retry_base_seconds = max(0.1, float(retry_base_seconds))
        self.apply_poll_seconds = max(0.05, float(apply_poll_seconds))

    async def process(self, claim: FactExtractionJobClaim) -> FactExtractionProcessResult:
        try:
            if claim.stage == "apply":
                return await self._apply(claim)
            prepared = await self._prepare(claim)
            if isinstance(prepared, FactExtractionProcessResult):
                return prepared
            inputs = await self.extractor.extract(
                context=FactExtractionContext(
                    owner_user_id=prepared.owner_user_id,
                    run_id=prepared.run_id,
                    source_message_id=prepared.message_id,
                    source_text=prepared.source_text,
                    recent_dialogue=prepared.recent_dialogue,
                    observed_at=prepared.observed_at,
                    trace_id=prepared.trace_id,
                )
            )
            stored = await self._store_candidates(
                claim=claim,
                candidates=fact_inputs_to_candidate_payload(inputs),
                extracted_count=len(inputs),
            )
            return FactExtractionProcessResult(status="ready_to_apply" if stored else "stale_lease")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = await self._reschedule(claim=claim, error_code=type(exc).__name__)
            return FactExtractionProcessResult(status=status)

    async def _prepare(
        self,
        claim: FactExtractionJobClaim,
    ) -> PreparedFactExtraction | FactExtractionProcessResult:
        async with self.session_factory() as session:
            fact_repository = AgentFactRepository(session)
            job = await fact_repository.get_claimed_extraction(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                for_update=True,
            )
            if job is None:
                return FactExtractionProcessResult(status="stale_lease")
            memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
            if not await memory_service.is_memory_enabled(owner_user_id=job.owner_user_id):
                await fact_repository.mark_skipped_disabled(job=job, completed_at=_utcnow())
                await session.commit()
                return FactExtractionProcessResult(status="skipped_disabled")
            now = _utcnow()
            if _candidate_retention_deadline(job.created_at) <= now:
                await fact_repository.mark_retention_expired(job=job, completed_at=now)
                await session.commit()
                return FactExtractionProcessResult(status="expired")
            runtime_repository = AgentRuntimeRepository(session)
            run = await runtime_repository.get_run_for_owner(
                run_id=job.source_run_id,
                owner_user_id=job.owner_user_id,
            )
            message = await runtime_repository.get_latest_user_message_for_run(run_id=job.source_run_id)
            if run is None or message is None or message.id != job.source_message_id:
                raise RuntimeError("Fact extraction source message is unavailable.")
            messages = await runtime_repository.list_messages_for_thread(thread_id=run.thread_id, limit=10)
            dialogue = tuple(
                (item.role, _message_text(item.content))
                for item in messages
                if item.sequence <= message.sequence
                and item.role in {"user", "assistant"}
                and _message_text(item.content)
            )[-5:]
            prepared = PreparedFactExtraction(
                owner_user_id=job.owner_user_id,
                run_id=job.source_run_id,
                message_id=job.source_message_id,
                source_text=_message_text(message.content),
                recent_dialogue=dialogue,
                observed_at=message.created_at if isinstance(message.created_at, datetime) else _utcnow(),
                request_id=job.request_id,
                trace_id=job.trace_id,
            )
            await session.commit()
            return prepared

    async def _store_candidates(
        self,
        *,
        claim: FactExtractionJobClaim,
        candidates: list[dict[str, Any]],
        extracted_count: int,
    ) -> bool:
        async with self.session_factory() as session:
            repository = AgentFactRepository(session)
            job = await repository.get_claimed_extraction(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                for_update=True,
            )
            if job is None:
                return False
            await repository.mark_ready_to_apply(
                job=job,
                candidates=candidates,
                extracted_count=extracted_count,
                rejected_count=0,
                next_attempt_at=_utcnow(),
            )
            await session.commit()
            return True

    async def _apply(self, claim: FactExtractionJobClaim) -> FactExtractionProcessResult:
        async with self.session_factory() as session:
            repository = AgentFactRepository(session)
            job = await repository.get_claimed_extraction(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                for_update=True,
            )
            if job is None:
                return FactExtractionProcessResult(status="stale_lease")
            memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
            if not await memory_service.is_memory_enabled(owner_user_id=job.owner_user_id):
                await repository.mark_skipped_disabled(job=job, completed_at=_utcnow())
                await session.commit()
                return FactExtractionProcessResult(status="skipped_disabled")
            now = _utcnow()
            if _candidate_retention_deadline(job.created_at) <= now:
                await repository.mark_retention_expired(job=job, completed_at=now)
                await session.commit()
                return FactExtractionProcessResult(status="expired")
            runtime_repository = AgentRuntimeRepository(session)
            run = await runtime_repository.get_run_for_owner(
                run_id=job.source_run_id,
                owner_user_id=job.owner_user_id,
            )
            if run is None:
                raise RuntimeError("Fact extraction source run is unavailable.")
            if run.status not in TERMINAL_SOURCE_RUN_STATUSES:
                await repository.defer_apply(
                    job=job,
                    next_attempt_at=now + timedelta(seconds=self.apply_poll_seconds),
                )
                await session.commit()
                return FactExtractionProcessResult(status="deferred")
            message = await runtime_repository.get_latest_user_message_for_run(run_id=job.source_run_id)
            if message is None or message.id != job.source_message_id:
                raise RuntimeError("Fact extraction source message is unavailable.")
            fact_service = AgentFactService(
                repository=repository,
                audit_service=AuditService(repository=AuditRepository(session)),
                memory_consent_reader=memory_service,
                extraction_enabled=False,
            )
            result = await fact_service.apply_candidate_payloads(
                owner_user_id=job.owner_user_id,
                source_message_id=job.source_message_id,
                observed_at=message.created_at if isinstance(message.created_at, datetime) else _utcnow(),
                candidates=job.candidates,
                request_id=job.request_id,
            )
            await repository.mark_completed(
                job=job,
                applied_count=result.applied_count,
                rejected_count=job.rejected_count + result.rejected_count,
                completed_at=_utcnow(),
            )
            await session.commit()
            return FactExtractionProcessResult(status="completed", applied_count=result.applied_count)

    async def _reschedule(self, *, claim: FactExtractionJobClaim, error_code: str) -> str:
        async with self.session_factory() as session:
            repository = AgentFactRepository(session)
            job = await repository.get_claimed_extraction(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                for_update=True,
            )
            if job is None:
                return "stale_lease"
            delay_seconds = min(60.0, self.retry_base_seconds * (2 ** max(0, job.attempts - 1)))
            await repository.reschedule_or_dead_letter(
                job=job,
                next_attempt_at=_utcnow() + timedelta(seconds=delay_seconds),
                error_code=error_code,
                completed_at=_utcnow(),
            )
            status = str(job.status)
            await session.commit()
            return status


def _message_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    value = content.get("text")
    return str(value).strip() if isinstance(value, str) else ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _candidate_retention_deadline(created_at: datetime) -> datetime:
    normalized = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
    return normalized + PENDING_CANDIDATE_RETENTION


__all__ = [
    "AgentFactExtractionWorker",
    "FactExtractionProcessResult",
    "PreparedFactExtraction",
]
