from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..consultations.room_models import CareConsultation
from .models import CarePlanDraft, CarePlanPublication, CareTaskProgress, ClinicalNote


class DocumentationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_note(self, consultation_id: UUID) -> ClinicalNote | None:
        return cast(ClinicalNote | None, await self.session.scalar(select(ClinicalNote).where(ClinicalNote.consultation_id == consultation_id)
            .order_by(ClinicalNote.revision.desc()).limit(1).execution_options(populate_existing=True)))

    async def note(self, consultation_id: UUID, note_id: UUID) -> ClinicalNote | None:
        return cast(ClinicalNote | None, await self.session.scalar(select(ClinicalNote).where(ClinicalNote.consultation_id == consultation_id, ClinicalNote.id == note_id)))

    async def note_history(self, consultation_id: UUID) -> list[ClinicalNote]:
        return list(await self.session.scalars(select(ClinicalNote).where(ClinicalNote.consultation_id == consultation_id).order_by(ClinicalNote.revision.desc())))

    async def plan(self, consultation_id: UUID) -> CarePlanDraft | None:
        return cast(CarePlanDraft | None, await self.session.scalar(select(CarePlanDraft).where(CarePlanDraft.consultation_id == consultation_id).execution_options(populate_existing=True)))

    async def latest_publication(self, plan_id: UUID) -> CarePlanPublication | None:
        return cast(CarePlanPublication | None, await self.session.scalar(select(CarePlanPublication).where(CarePlanPublication.plan_id == plan_id)
            .order_by(CarePlanPublication.revision.desc()).limit(1)))

    async def publication(self, publication_id: UUID) -> CarePlanPublication | None:
        return await self.session.get(CarePlanPublication, publication_id)

    async def publication_appointment(self, publication_id: UUID) -> UUID | None:
        return cast(UUID | None, await self.session.scalar(select(CareConsultation.appointment_id).join(CarePlanDraft, CarePlanDraft.consultation_id == CareConsultation.id)
            .join(CarePlanPublication, CarePlanPublication.plan_id == CarePlanDraft.id).where(CarePlanPublication.id == publication_id)))

    async def progress(self, publication_id: UUID) -> list[CareTaskProgress]:
        return list(await self.session.scalars(select(CareTaskProgress).where(CareTaskProgress.publication_id == publication_id)))

    async def task(self, publication_id: UUID, source_key: str) -> CareTaskProgress | None:
        return cast(CareTaskProgress | None, await self.session.scalar(select(CareTaskProgress).where(
            CareTaskProgress.publication_id == publication_id, CareTaskProgress.source_key == source_key).execution_options(populate_existing=True)))

    async def add(self, value: Any) -> None:
        self.session.add(value)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
