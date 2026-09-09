from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...core.errors import ApiError
from ..care.models import CareEpisode, CareProvider
from ..care.repository import CareRepository
from ..consultations.repository import ConsultationRepository
from ..users.models import User


@dataclass(frozen=True)
class ReportAccess:
    episode: CareEpisode
    timezone: str
    case_version: int
    ai_version: int

    @property
    def authority(self) -> tuple[UUID | None, int, int]:
        return self.episode.assigned_ibclc_id, self.case_version, self.ai_version


async def report_access(session: AsyncSession, provider_id: UUID, episode_id: UUID, *, lock: bool = False) -> ReportAccess:
    owner = aliased(User)
    query = select(CareEpisode).join(CareProvider, CareProvider.user_id == CareEpisode.assigned_ibclc_id).join(User, User.id == CareProvider.user_id).join(owner, owner.id == CareEpisode.owner_user_id).where(
        CareEpisode.id == episode_id, CareEpisode.assigned_ibclc_id == provider_id,
        CareEpisode.owner_user_id != provider_id, CareProvider.active.is_(True), User.status == 'active', User.deleted_at.is_(None),
        owner.status == 'active', owner.deleted_at.is_(None))
    episode = await session.scalar(query.execution_options(populate_existing=True))
    if episode is None:
        raise ApiError(code='not_found', message='Service not found.', status=404)
    if lock:
        await CareRepository(session).lock_owner(episode.owner_user_id)
        episode = await session.scalar(query.with_for_update(of=CareEpisode).execution_options(populate_existing=True))
        if episode is None:
            raise ApiError(code='not_found', message='Service not found.', status=404)
    consents = ConsultationRepository(session)
    case, ai = await consents.consent(episode_id, 'ibclc_case'), await consents.consent(episode_id, 'ai_context')
    if case is None or not case.active:
        raise ApiError(code='case_consent_required', message='Case sharing authorization is required.', status=403)
    if ai is None or not ai.active:
        raise ApiError(code='ai_consent_required', message='AI context authorization is required.', status=403)
    zone = await session.scalar(select(CareProvider.timezone).where(CareProvider.user_id == provider_id))
    assert zone is not None
    return ReportAccess(episode, zone, case.version, ai.version)
