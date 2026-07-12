from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from .catalog import FACT_CATALOG_VERSION, facts_to_form_defaults, form_values_to_fact_inputs, normalize_fact_value
from .repository import AgentFactRepository
from .types import FactApplyResult, FactInput


SOURCE_PRIORITIES = {"conversation": 10, "form_submission": 20}


class AgentFactService:
    def __init__(self, *, repository: AgentFactRepository) -> None:
        self.repository = repository

    async def values(self, *, owner_user_id: UUID) -> dict[str, Any]:
        return {record.fact_key: record.value for record in await self.repository.list_active(owner_user_id=owner_user_id)}

    async def form_defaults(self, *, owner_user_id: UUID, form_id: str) -> dict[str, Any]:
        return facts_to_form_defaults(form_id=form_id, facts=await self.values(owner_user_id=owner_user_id))

    async def sync_form_submission(
        self,
        *,
        owner_user_id: UUID,
        form_id: str,
        values: dict[str, Any],
        submission_id: str,
        observed_at: datetime,
    ) -> FactApplyResult:
        return await self.apply_inputs(
            owner_user_id=owner_user_id,
            inputs=form_values_to_fact_inputs(form_id=form_id, values=values),
            source_type="form_submission",
            source_id=submission_id,
            observed_at=observed_at,
        )

    async def apply_inputs(
        self,
        *,
        owner_user_id: UUID,
        inputs: list[FactInput],
        source_type: str,
        source_id: str,
        observed_at: datetime,
    ) -> FactApplyResult:
        source_priority = SOURCE_PRIORITIES[source_type]
        applied_count = 0
        rejected_count = 0
        for item in inputs:
            if item.certainty != "explicit":
                rejected_count += 1
                continue
            try:
                value = normalize_fact_value(item.fact_key, item.value)
            except (TypeError, ValueError):
                rejected_count += 1
                continue
            applied = await self.repository.apply_fact(
                owner_user_id=owner_user_id,
                fact_key=item.fact_key,
                value=value,
                source_type=source_type,
                source_id=source_id,
                source_priority=source_priority,
                observed_at=observed_at,
                evidence=item.evidence,
                catalog_version=FACT_CATALOG_VERSION,
            )
            applied_count += int(applied)
        return FactApplyResult(applied_count=applied_count, rejected_count=rejected_count)


__all__ = ["AgentFactService", "FactApplyResult", "FactInput"]
