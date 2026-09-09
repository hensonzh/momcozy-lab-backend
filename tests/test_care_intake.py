
from app.modules.baby.profile_models import BabyProfile
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.appointments.schemas import BookingPrecheckWrite, HoldWrite, VersionWrite
from app.modules.auth import CurrentUser
from app.modules.consultations.models import CareConsentRevision, CareIntakeRevision
from app.modules.consultations.repository import ConsultationRepository
from app.modules.consultations.schemas import ConsentWrite, IntakeWrite
from app.modules.consultations.service import ConsultationService
from app.modules.profiles.models import MaternalProfile
from test_care_booking import database, postgres


def test_intake_contract_uses_dates_and_baby_identity_without_derived_age_or_client_risk():
    with pytest.raises(ValidationError):
        IntakeWrite.model_validate({'expected_version': 0, 'symptoms': ['latch'], 'feeding_goal': 'Goal', 'risk_level': 'R0'})
    with pytest.raises(ValidationError):
        ConsentWrite(expected_version=0, scope='ibclc_case', active=True, policy_version='unrecognized')


@postgres
def test_intake_revisions_scope_grants_and_rebooking_preserve_only_authorized_data():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            async with sessions.begin() as session:
                connection = await session.connection()
                await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[MaternalProfile.__table__, CareIntakeRevision.__table__, CareConsentRevision.__table__]))
                baby, foreign_baby = uuid4(), uuid4()
                session.add_all([BabyProfile(id=baby, owner_user_id=owners[0], name='Test baby', birth_date=(now-timedelta(days=21)).date(), sex='female'),
                    BabyProfile(id=foreign_baby, owner_user_id=owners[1], name='Other baby')])
                session.add(MaternalProfile(owner_user_id=owners[0], latest_delivery_date=(now-timedelta(days=21)).date()))
                check = await booking(session).precheck(owners[0], episodes[0], BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='clear'), 'precheck')
                available = await booking(session).availability(owners[0], episodes[0], check.id, provider, (now+timedelta(days=1)).date())
                slot = available.slots[0]
                held = await booking(session).hold(owners[0], episodes[0], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slot.starts_at), 'hold', 'hold')
                confirmed = await booking(session).confirm(owners[0], held.id, VersionWrite(expected_version=1), 'confirm')
            def service(session):
                return ConsultationService(ConsultationRepository(session), booking(session), AuditService(repository=AuditRepository(session)), now=lambda: now)
            expert = CurrentUser(user_id=provider, subject=str(provider), session_id='test', token_id='test', roles=frozenset({'ibclc'}), permissions=frozenset())
            wrong = CurrentUser(user_id=owners[1], subject=str(owners[1]), session_id='test', token_id='test', roles=frozenset({'ibclc'}), permissions=frozenset())
            payload = {'expected_version': 0, 'expected_consent_version': 0, 'symptoms': ['latch_difficulty'], 'feeding_goal': 'Reduce pain', 'support_needed': 'Only shared with assigned expert', 'consent_to_share': True, 'consent_policy_version': '2026-09-08',
                'profile': {'baby_id': str(baby), 'baby_name': 'Test baby', 'baby_birth_date': (now-timedelta(days=21)).date().isoformat(), 'baby_sex': 'female', 'feeding_mode': 'exclusive_breastfeeding', 'delivery_date': (now-timedelta(days=21)).date().isoformat(), 'region': 'CA'}}
            async with sessions.begin() as session:
                current = service(session)
                context = await current.intake_context(owners[0], confirmed.id)
                assert context.intake is None and context.previous_intake is None and context.babies[0].id == baby
                with pytest.raises(ApiError) as unshared:
                    await current.expert_intake(expert, confirmed.id)
                assert unshared.value.status == 403
                wrong_payload = {**payload, 'profile': {**payload['profile'], 'baby_id': str(foreign_baby)}}
                with pytest.raises(ApiError) as foreign:
                    await current.save_intake(owners[0], confirmed.id, IntakeWrite.model_validate(wrong_payload), 'foreign-baby')
                assert foreign.value.status == 404
                saved = await current.save_intake(owners[0], confirmed.id, IntakeWrite.model_validate(payload), 'save')
                replay = await current.save_intake(owners[0], confirmed.id, IntakeWrite.model_validate(payload), 'retry')
                assert saved.version == replay.version == 1
                assert (await current.expert_intake(expert, confirmed.id)).support_needed == payload['support_needed']
                with pytest.raises(ApiError) as unrelated:
                    await current.expert_intake(wrong, confirmed.id)
                assert unrelated.value.status == 404
            async with sessions.begin() as session:
                current = service(session)
                context = await current.intake_context(owners[0], confirmed.id)
                assert not any(item.scope == 'video' and item.active for item in context.consents)
                consent = next(item for item in context.consents if item.scope == 'ibclc_case')
                await current.set_consent(owners[0], episodes[0], ConsentWrite(expected_version=consent.version, scope='ibclc_case', active=False, policy_version='2026-09-08'), 'revoke')
                with pytest.raises(ApiError) as revoked:
                    await current.expert_intake(expert, confirmed.id)
                assert revoked.value.status == 403
                # Retrying an old save cannot silently undo a later explicit withdrawal.
                await current.save_intake(owners[0], confirmed.id, IntakeWrite.model_validate(payload), 'old-retry')
                with pytest.raises(ApiError):
                    await current.expert_intake(expert, confirmed.id)
                assert (await current.intake_context(owners[0], confirmed.id)).intake is not None
                revisions = list(await session.scalars(select(CareIntakeRevision).where(CareIntakeRevision.appointment_id == confirmed.id)))
                assert len(revisions) == 1
            async with sessions.begin() as session:
                await booking(session).cancel(owners[0], confirmed.id, VersionWrite(expected_version=3), 'cancel')
                new_hold = await booking(session).hold(owners[0], episodes[0], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slot.starts_at), 'rebook', 'rebook')
                new_appointment = await booking(session).confirm(owners[0], new_hold.id, VersionWrite(expected_version=1), 'confirm-new')
                context = await service(session).intake_context(owners[0], new_appointment.id)
                assert context.intake is None and context.previous_intake.id == saved.id
                assert not any(item.active and item.scope == 'ibclc_case' for item in context.consents)
    asyncio.run(run())
