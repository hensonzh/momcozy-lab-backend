from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, Request, Response
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.agent_runtime import RuntimeConversationGateway
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from ..auth import CurrentUser
from .conversation_schemas import CareConversationLinkRead, CareConversationLinksRead, CareConversationLinkWrite
from .conversations import CareConversationService

router = SurfaceAPIRouter(prefix='/care/episodes/{episode_id}/conversations', tags=['care-conversations'],
    api_surface_metadata=api_surface('public_app_api', owner='reports', clients=['flutter']))


async def get_conversation_service(request: Request, response: Response, session: AsyncSession = Depends(get_session)) -> AsyncIterator[CareConversationService]:
    response.headers['Cache-Control'] = 'private, no-store'
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        yield CareConversationService(session, RuntimeConversationGateway(client, request.app.state.settings.care_report_runtime_url),
            AuditService(repository=AuditRepository(session)))


@router.get('', response_model=CareConversationLinksRead)
async def conversations(episode_id: UUID, actor: CurrentUser = Depends(require_current_user), service: CareConversationService = Depends(get_conversation_service)) -> CareConversationLinksRead:
    return CareConversationLinksRead(items=await service.list(actor.user_id, episode_id))


@router.put('/{thread_id}', response_model=CareConversationLinkRead)
async def link(episode_id: UUID, thread_id: UUID, body: CareConversationLinkWrite, request: Request,
    actor: CurrentUser = Depends(require_current_user), service: CareConversationService = Depends(get_conversation_service)) -> CareConversationLinkRead:
    return await service.link(actor.user_id, episode_id, thread_id, request.headers.get('Authorization', ''), request.state.request_id)
