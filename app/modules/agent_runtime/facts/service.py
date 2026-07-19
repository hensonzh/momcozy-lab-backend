from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from ....core.errors import ApiError
from ...audit import AuditService
from .catalog import (
    FACT_CATALOG_VERSION,
    FACT_FIELD_BY_KEY,
    candidate_value_contains_restricted_content,
    candidate_value_matches_safe_shape,
    conversation_candidate_ttl_days,
    expected_conversation_subject,
    facts_to_form_defaults,
    form_values_to_fact_inputs,
    normalize_fact_value,
)
from .models import UserFact, UserFactExtractionRun
from .extraction import candidate_payload_to_inputs
from .repository import AgentFactRepository
from .types import FactApplyResult, FactInput


FACT_KIND_VERIFIED = "verified"
FACT_KIND_CONVERSATION_CANDIDATE = "conversation_candidate"
SOURCE_TYPE_FORM = "form_submission"
SOURCE_TYPE_CONVERSATION = "conversation"
DEFAULT_EXTRACTION_MODEL = "gpt-5.4-nano"
DEFAULT_EXTRACTION_VERSION = "turn-fact-extractor-v2"
DEFAULT_EXTRACTION_MAX_ATTEMPTS = 3


class MemoryConsentReader(Protocol):
    async def is_memory_enabled(self, *, owner_user_id: UUID) -> bool: ...


class AgentFactService:
    def __init__(
        self,
        *,
        repository: AgentFactRepository,
        audit_service: AuditService | None = None,
        memory_consent_reader: MemoryConsentReader | None = None,
        extraction_enabled: bool = True,
        extraction_model: str = DEFAULT_EXTRACTION_MODEL,
        extraction_version: str = DEFAULT_EXTRACTION_VERSION,
        extraction_max_attempts: int = DEFAULT_EXTRACTION_MAX_ATTEMPTS,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.memory_consent_reader = memory_consent_reader
        self.extraction_enabled = bool(extraction_enabled)
        self.extraction_model = extraction_model.strip() or DEFAULT_EXTRACTION_MODEL
        self.extraction_version = extraction_version.strip() or DEFAULT_EXTRACTION_VERSION
        self.extraction_max_attempts = max(1, int(extraction_max_attempts))

    async def is_capture_enabled(self, *, owner_user_id: UUID) -> bool:
        if self.memory_consent_reader is None:
            return True
        return await self.memory_consent_reader.is_memory_enabled(owner_user_id=owner_user_id)

    async def values(self, *, owner_user_id: UUID) -> dict[str, Any]:
        if not await self.is_capture_enabled(owner_user_id=owner_user_id):
            return {}
        records = await self.repository.list_active(owner_user_id=owner_user_id)
        resolved: dict[str, tuple[int, Any]] = {}
        for record in records:
            if record.value is None:
                continue
            rank = 2 if record.fact_kind == FACT_KIND_VERIFIED else 1
            current = resolved.get(record.fact_key)
            if current is None or rank > current[0]:
                resolved[record.fact_key] = (rank, record.value)
        return {fact_key: value for fact_key, (_rank, value) in resolved.items()}

    async def form_defaults(self, *, owner_user_id: UUID, form_id: str) -> dict[str, Any]:
        return facts_to_form_defaults(form_id=form_id, facts=await self.values(owner_user_id=owner_user_id))

    async def list_facts(
        self,
        *,
        owner_user_id: UUID,
        fact_kind: str | None,
        limit: int,
        include_when_disabled: bool = True,
    ) -> list[UserFact]:
        normalized_kind = _normalize_optional_fact_kind(fact_kind)
        if not include_when_disabled and not await self.is_capture_enabled(owner_user_id=owner_user_id):
            return []
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_for_owner(
            owner_user_id=owner_user_id,
            fact_kind=normalized_kind,
            limit=limit,
        )

    async def sync_form_submission(
        self,
        *,
        owner_user_id: UUID,
        form_id: str,
        values: dict[str, Any],
        submission_id: str,
        observed_at: datetime,
        request_id: str = "",
    ) -> FactApplyResult:
        if not await self.is_capture_enabled(owner_user_id=owner_user_id):
            return FactApplyResult(applied_count=0, rejected_count=0)
        return await self.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=form_values_to_fact_inputs(form_id=form_id, values=values),
            source_type=SOURCE_TYPE_FORM,
            source_id=submission_id,
            observed_at=observed_at,
            request_id=request_id,
        )

    async def apply_inputs(
        self,
        *,
        owner_user_id: UUID,
        inputs: list[FactInput],
        source_type: str,
        source_id: str,
        observed_at: datetime,
        request_id: str = "",
    ) -> FactApplyResult:
        fact_kind = _fact_kind_for_source(source_type)
        applied_count = 0
        rejected_count = 0
        for item in inputs:
            field = FACT_FIELD_BY_KEY.get(item.fact_key)
            if item.certainty != "explicit" or field is None:
                rejected_count += 1
                continue
            if fact_kind == FACT_KIND_CONVERSATION_CANDIDATE and not field.candidate_eligible:
                rejected_count += 1
                continue
            if (
                fact_kind == FACT_KIND_CONVERSATION_CANDIDATE
                and item.subject != expected_conversation_subject(item.fact_key)
            ):
                rejected_count += 1
                continue
            try:
                value = normalize_fact_value(item.fact_key, item.value)
            except (TypeError, ValueError):
                rejected_count += 1
                continue
            if (
                fact_kind == FACT_KIND_CONVERSATION_CANDIDATE
                and (
                    candidate_value_contains_restricted_content(value)
                    or not candidate_value_matches_safe_shape(fact_key=item.fact_key, value=value)
                )
            ):
                rejected_count += 1
                continue
            expires_at = (
                observed_at + timedelta(days=conversation_candidate_ttl_days(item.fact_key))
                if fact_kind == FACT_KIND_CONVERSATION_CANDIDATE
                else None
            )
            fact, applied = await self.repository.apply_fact(
                owner_user_id=owner_user_id,
                fact_key=item.fact_key,
                fact_kind=fact_kind,
                value=value,
                source_type=source_type,
                source_id=source_id,
                sensitivity=field.sensitivity,
                observed_at=observed_at,
                expires_at=expires_at,
                catalog_version=FACT_CATALOG_VERSION,
            )
            if not applied:
                continue
            applied_count += 1
            await self._record_audit(
                owner_user_id=owner_user_id,
                fact=fact,
                action=(
                    "agent.fact.verified.upsert"
                    if fact_kind == FACT_KIND_VERIFIED
                    else "agent.fact.candidate.upsert"
                ),
                actor_type="user" if fact_kind == FACT_KIND_VERIFIED else "service",
                actor_service="" if fact_kind == FACT_KIND_VERIFIED else "agent-fact-worker",
                request_id=request_id,
                details={"source_type": source_type},
            )
            if fact_kind == FACT_KIND_VERIFIED:
                promoted = await self.repository.tombstone_kind_for_owner_key(
                    owner_user_id=owner_user_id,
                    fact_key=item.fact_key,
                    fact_kind=FACT_KIND_CONVERSATION_CANDIDATE,
                    deleted_at=observed_at,
                    deletion_reason="promoted",
                )
                if promoted is not None:
                    await self._record_audit(
                        owner_user_id=owner_user_id,
                        fact=promoted,
                        action="agent.fact.candidate.promoted",
                        actor_type="user",
                        actor_service="",
                        request_id=request_id,
                        details={"deletion_reason": "promoted"},
                    )
        return FactApplyResult(applied_count=applied_count, rejected_count=rejected_count)

    async def enqueue_conversation_extraction(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        message_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> UserFactExtractionRun | None:
        if not self.extraction_enabled or not await self.is_capture_enabled(owner_user_id=owner_user_id):
            return None
        return await self.repository.enqueue_extraction(
            owner_user_id=owner_user_id,
            source_message_id=message_id,
            source_run_id=run_id,
            catalog_version=FACT_CATALOG_VERSION,
            extractor_version=self.extraction_version,
            model=self.extraction_model,
            max_attempts=self.extraction_max_attempts,
            request_id=request_id,
            trace_id=trace_id,
            next_attempt_at=datetime.now(timezone.utc),
        )

    async def apply_candidate_payloads(
        self,
        *,
        owner_user_id: UUID,
        source_message_id: UUID,
        observed_at: datetime,
        candidates: list[dict[str, Any]],
        request_id: str = "",
    ) -> FactApplyResult:
        if not await self.is_capture_enabled(owner_user_id=owner_user_id):
            return FactApplyResult(applied_count=0, rejected_count=len(candidates))
        inputs = candidate_payload_to_inputs(candidates)
        return await self.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=inputs,
            source_type=SOURCE_TYPE_CONVERSATION,
            source_id=str(source_message_id),
            observed_at=observed_at,
            request_id=request_id,
        )

    async def delete_fact(
        self,
        *,
        owner_user_id: UUID,
        fact_id: UUID,
        request_id: str = "",
    ) -> UserFact:
        deleted_at = datetime.now(timezone.utc)
        existing = await self.repository.get_for_owner(
            owner_user_id=owner_user_id,
            fact_id=fact_id,
        )
        if existing is None:
            raise ApiError(code="not_found", message="Agent fact not found.", status=404)
        if existing.status == "tombstoned":
            return existing
        cancelled_jobs = await self.repository.cancel_pending_extractions_for_owner(
            owner_user_id=owner_user_id,
            completed_at=deleted_at,
            reason="user_deleted_fact",
        )
        deleted_facts = await self.repository.tombstone_all_kinds_for_owner_key(
            owner_user_id=owner_user_id,
            fact_key=existing.fact_key,
            deleted_at=deleted_at,
            deletion_reason="user_deleted",
        )
        for fact in deleted_facts:
            await self._record_audit(
                owner_user_id=owner_user_id,
                fact=fact,
                action="agent.fact.delete",
                actor_type="user",
                actor_service="",
                request_id=request_id,
                details={
                    "deletion_reason": "user_deleted",
                    "cancelled_extraction_count": len(cancelled_jobs),
                },
            )
        return next((fact for fact in deleted_facts if fact.id == fact_id), existing)

    async def clear_facts(self, *, owner_user_id: UUID, request_id: str = "") -> int:
        deleted_at = datetime.now(timezone.utc)
        cancelled_jobs = await self.repository.cancel_pending_extractions_for_owner(
            owner_user_id=owner_user_id,
            completed_at=deleted_at,
            reason="user_cleared",
        )
        facts = await self.repository.tombstone_all_for_owner(
            owner_user_id=owner_user_id,
            deleted_at=deleted_at,
            deletion_reason="user_deleted",
        )
        for fact in facts:
            await self._record_audit(
                owner_user_id=owner_user_id,
                fact=fact,
                action="agent.fact.delete",
                actor_type="user",
                actor_service="",
                request_id=request_id,
                details={"deletion_reason": "user_deleted"},
            )
        await self._record_owner_audit(
            owner_user_id=owner_user_id,
            action="agent.fact.clear",
            request_id=request_id,
            details={
                "deleted_fact_count": len(facts),
                "cancelled_extraction_count": len(cancelled_jobs),
            },
        )
        return len(facts)

    async def cancel_pending_extractions(
        self,
        *,
        owner_user_id: UUID,
        reason: str,
        request_id: str = "",
    ) -> int:
        jobs = await self.repository.cancel_pending_extractions_for_owner(
            owner_user_id=owner_user_id,
            completed_at=datetime.now(timezone.utc),
            reason=reason,
        )
        await self._record_owner_audit(
            owner_user_id=owner_user_id,
            action="agent.fact.extraction.cancel_pending",
            request_id=request_id,
            details={"reason": reason[:120], "cancelled_extraction_count": len(jobs)},
        )
        return len(jobs)

    async def _record_audit(
        self,
        *,
        owner_user_id: UUID,
        fact: UserFact,
        action: str,
        actor_type: str,
        actor_service: str,
        request_id: str,
        details: dict[str, Any],
    ) -> None:
        if self.audit_service is None:
            return
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            actor_type=actor_type,
            actor_service=actor_service,
            action=action,
            resource_type="agent_user_fact",
            resource_id=str(fact.id),
            request_id=request_id,
            details={
                "fact_key": fact.fact_key,
                "fact_kind": fact.fact_kind,
                **details,
            },
        )

    async def _record_owner_audit(
        self,
        *,
        owner_user_id: UUID,
        action: str,
        request_id: str,
        details: dict[str, Any],
    ) -> None:
        if self.audit_service is None:
            return
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            actor_type="user",
            actor_service="",
            action=action,
            resource_type="agent_user_facts",
            resource_id=str(owner_user_id),
            request_id=request_id,
            details=details,
        )


def _fact_kind_for_source(source_type: str) -> str:
    normalized = source_type.strip()
    if normalized in {SOURCE_TYPE_FORM, "verified_form"}:
        return FACT_KIND_VERIFIED
    if normalized in {SOURCE_TYPE_CONVERSATION, "conversation_candidate"}:
        return FACT_KIND_CONVERSATION_CANDIDATE
    raise ValueError("unsupported fact source type")


def _normalize_optional_fact_kind(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if normalized not in {FACT_KIND_VERIFIED, FACT_KIND_CONVERSATION_CANDIDATE}:
        raise ApiError(code="validation_failed", message="fact_kind is not supported.", status=422)
    return normalized


__all__ = [
    "AgentFactService",
    "FactApplyResult",
    "FactInput",
    "FACT_KIND_CONVERSATION_CANDIDATE",
    "FACT_KIND_VERIFIED",
]
