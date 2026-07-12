from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserFact, UserFactExtractionRun


class AgentFactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, *, owner_user_id: UUID) -> list[UserFact]:
        statement = select(UserFact).where(UserFact.owner_user_id == owner_user_id).order_by(UserFact.fact_key)
        return list((await self.session.scalars(statement)).all())

    async def apply_fact(
        self,
        *,
        owner_user_id: UUID,
        fact_key: str,
        value: Any,
        source_type: str,
        source_id: str,
        source_priority: int,
        observed_at: datetime,
        evidence: str,
        catalog_version: str,
    ) -> bool:
        statement = (
            select(UserFact)
            .where(UserFact.owner_user_id == owner_user_id, UserFact.fact_key == fact_key)
            .with_for_update()
        )
        fact = cast(UserFact | None, await self.session.scalar(statement))
        if fact is not None and (observed_at, source_priority) <= (fact.observed_at, fact.source_priority):
            return False
        if fact is None:
            fact = UserFact(owner_user_id=owner_user_id, fact_key=fact_key)
            self.session.add(fact)
        fact.value = value
        fact.source_type = source_type
        fact.source_id = source_id[:255]
        fact.source_priority = source_priority
        fact.observed_at = observed_at
        fact.evidence = evidence[:500]
        fact.catalog_version = catalog_version
        await self.session.flush()
        return True

    async def begin_extraction(
        self,
        *,
        owner_user_id: UUID,
        source_message_id: UUID,
        source_run_id: UUID,
        catalog_version: str,
        extractor_version: str,
        model: str,
    ) -> UserFactExtractionRun | None:
        statement = select(UserFactExtractionRun).where(
            UserFactExtractionRun.owner_user_id == owner_user_id,
            UserFactExtractionRun.source_message_id == source_message_id,
            UserFactExtractionRun.catalog_version == catalog_version,
            UserFactExtractionRun.extractor_version == extractor_version,
        )
        existing = cast(UserFactExtractionRun | None, await self.session.scalar(statement))
        if existing is not None and existing.status in {"extracting", "completed"}:
            return None
        if existing is None:
            existing = UserFactExtractionRun(
                owner_user_id=owner_user_id,
                source_message_id=source_message_id,
                source_run_id=source_run_id,
                catalog_version=catalog_version,
                extractor_version=extractor_version,
                model=model,
                status="extracting",
            )
            self.session.add(existing)
        else:
            existing.status = "extracting"
            existing.model = model
            existing.error_code = ""
            existing.completed_at = None
        await self.session.flush()
        return existing

    async def complete_extraction(
        self,
        *,
        extraction: UserFactExtractionRun,
        extracted_count: int,
        applied_count: int,
    ) -> None:
        extraction.status = "completed"
        extraction.extracted_count = extracted_count
        extraction.applied_count = applied_count
        extraction.error_code = ""
        extraction.completed_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def fail_extraction(self, *, extraction: UserFactExtractionRun, error_code: str) -> None:
        extraction.status = "failed"
        extraction.error_code = error_code[:120]
        extraction.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
