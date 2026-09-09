import asyncio
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.consultations.models import CareConsentRevision
from app.modules.reports.conversations import CareConversationService
from test_care_booking import postgres
from test_care_rooms import room_case


class ThreadGateway:
    def __init__(self, owner, thread):
        self.owner, self.thread, self.calls = owner, thread, 0
        self.on_verify = None
    async def verify_owner(self, owner, thread, authorization):
        self.calls += 1
        assert authorization == 'Bearer synthetic-test-token'
        if (owner, thread) != (self.owner, self.thread):
            raise ApiError(code='not_found', message='Conversation not found.', status=404)
        if self.on_verify:
            await self.on_verify()


@postgres
def test_conversation_sharing_requires_current_scopes_and_verifies_runtime_owner():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            thread = uuid4()
            gateway = ThreadGateway(mom.user_id, thread)
            def service(session):
                return CareConversationService(session, gateway, AuditService(repository=AuditRepository(session)), now=lambda: at[0])
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as consent:
                    await service(session).link(mom.user_id, appointment.episode_id, thread, 'Bearer synthetic-test-token', 'missing-ai')
                assert consent.value.code == 'care_sharing_not_authorized' and gateway.calls == 0
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=1, active=True, policy_version='2026-09-08', recorded_at=at[0]))
                await session.flush()
                with pytest.raises(ApiError) as foreign:
                    await service(session).link(wrong.user_id, appointment.episode_id, thread, 'Bearer synthetic-test-token', 'foreign')
                assert foreign.value.status == 404 and gateway.calls == 0
                with pytest.raises(ApiError) as other_thread:
                    await service(session).link(mom.user_id, appointment.episode_id, uuid4(), 'Bearer synthetic-test-token', 'other-thread')
                assert other_thread.value.status == 404
                first = await service(session).link(mom.user_id, appointment.episode_id, thread, 'Bearer synthetic-test-token', 'link')
                repeated = await service(session).link(mom.user_id, appointment.episode_id, thread, 'Bearer synthetic-test-token', 'repeat')
                assert first == repeated and first.shared_since == at[0]
                assert len(await service(session).list(mom.user_id, appointment.episode_id)) == 1
    asyncio.run(run())


@postgres
def test_authorization_withdrawn_during_runtime_verification_cannot_create_a_link():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            thread = uuid4()
            gateway = ThreadGateway(mom.user_id, thread)
            async with sessions.begin() as session:
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=1, active=True, policy_version='2026-09-08', recorded_at=at[0]))
            async def withdraw():
                async with sessions.begin() as session:
                    session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=2, active=False, policy_version='2026-09-08', recorded_at=at[0]))
            gateway.on_verify = withdraw
            async with sessions.begin() as session:
                service = CareConversationService(session, gateway, AuditService(repository=AuditRepository(session)), now=lambda: at[0])
                with pytest.raises(ApiError) as denied:
                    await service.link(mom.user_id, appointment.episode_id, thread, 'Bearer synthetic-test-token', 'inflight')
                assert denied.value.code == 'care_sharing_not_authorized'
                assert await service.list(mom.user_id, appointment.episode_id) == []
    asyncio.run(run())
