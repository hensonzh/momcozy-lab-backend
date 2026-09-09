from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..audit.service import AuditService
from ..care.models import CareEpisode, CareProvider
from ..care.repository import CareRepository
from ..consultations.repository import ConsultationRepository
from .conversation_schemas import CareConversationLinkRead
from .models import CareConversationLink


class ConversationOwnerGateway(Protocol):
    async def verify_owner(self, owner: UUID, thread: UUID, authorization: str) -> None: ...


class CareConversationService:
    def __init__(self, session: AsyncSession, gateway: ConversationOwnerGateway, audit: AuditService,
        *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.session, self.gateway, self.audit, self.now = session, gateway, audit, now

    async def _episode(self, owner: UUID, episode_id: UUID, *, lock: bool = False) -> CareEpisode:
        if lock:
            await CareRepository(self.session).lock_owner(owner)
        query = select(CareEpisode).where(CareEpisode.id == episode_id, CareEpisode.owner_user_id == owner)
        if lock:
            query = query.with_for_update()
        result = await self.session.scalar(query.execution_options(populate_existing=True))
        if result is None:
            raise ApiError(code='not_found', message='Service not found.', status=404)
        return result

    async def _authorized(self, owner: UUID, episode_id: UUID, *, lock: bool = False) -> tuple[int, int, UUID]:
        episode = await self._episode(owner, episode_id, lock=lock)
        provider = await self.session.scalar(select(CareProvider).where(CareProvider.user_id == episode.assigned_ibclc_id)
            .execution_options(populate_existing=True)) if episode.assigned_ibclc_id else None
        if episode.status != 'active' or (episode.ends_at is not None and episode.ends_at <= self.now()) or provider is None or not provider.active:
            raise ApiError(code='care_sharing_unavailable', message='This service is not available for shared conversations.', status=409)
        consents = ConsultationRepository(self.session)
        case, ai = await consents.consent(episode_id, 'ibclc_case'), await consents.consent(episode_id, 'ai_context')
        if case is None or ai is None or not case.active or not ai.active:
            raise ApiError(code='care_sharing_not_authorized', message='Case sharing and AI context authorization are required.', status=403)
        return case.version, ai.version, provider.user_id

    async def link(self, owner: UUID, episode_id: UUID, thread_id: UUID, authorization: str, request_id: str) -> CareConversationLinkRead:
        before = await self._authorized(owner, episode_id)
        await self.gateway.verify_owner(owner, thread_id, authorization)
        after = await self._authorized(owner, episode_id, lock=True)
        if before != after:
            raise ApiError(code='care_sharing_changed', message='Sharing authorization changed. Review it and try again.', status=409)
        existing = await self.session.get(CareConversationLink, thread_id)
        if existing is not None:
            if existing.episode_id != episode_id:
                raise ApiError(code='conversation_already_linked', message='This conversation belongs to another service.', status=409)
            return CareConversationLinkRead.model_validate(existing)
        link = CareConversationLink(thread_id=thread_id, episode_id=episode_id, shared_since=self.now())
        self.session.add(link)
        await self.session.flush()
        await self.audit.record(actor_user_id=owner, action='care.conversation.linked', resource_type='care_episode', resource_id=str(episode_id),
            request_id=request_id, details={'thread_id': str(thread_id), 'case_consent_version': after[0], 'ai_consent_version': after[1]})
        return CareConversationLinkRead.model_validate(link)

    async def list(self, owner: UUID, episode_id: UUID) -> list[CareConversationLinkRead]:
        await self._episode(owner, episode_id)
        records = await self.session.scalars(select(CareConversationLink).where(CareConversationLink.episode_id == episode_id).order_by(CareConversationLink.shared_since, CareConversationLink.thread_id))
        return [CareConversationLinkRead.model_validate(value) for value in records]
