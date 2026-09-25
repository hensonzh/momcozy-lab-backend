import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.modules.appointments.schemas import VersionWrite
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.care.models import CareEpisode
from app.modules.consultations.room_schemas import EndWrite
from app.modules.consultations.models import CareConsentRevision
from app.modules.documentation.models import CarePlanPublication, CareTaskProgress, ClinicalNote
from app.modules.documentation.repository import DocumentationRepository
from app.modules.documentation.schemas import NoteAmendWrite, NoteContent, NoteVersionWrite, NoteWrite, PlanContent, PlanWrite, TaskProgressWrite
from app.modules.documentation.service import DocumentationService
from test_care_booking import postgres
from test_care_rooms import enter, room_case


@asynccontextmanager
async def documented_case():
    async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
        await enter(sessions, rooms, at, appointment, mom, expert)
        async with sessions.begin() as session:
            started = await rooms(session).start(expert, appointment.id, VersionWrite(expected_version=1), 'start')
            await rooms(session).end(expert, appointment.id, EndWrite(expected_version=started.consultation.version, reason='completed'), 'complete')
        def service(session):
            audit = AuditRepository(session)
            return DocumentationService(DocumentationRepository(session), booking(session, at=at[0]), AuditService(repository=audit), IdempotencyService(repository=audit), now=lambda: at[0])
        yield sessions, service, at, appointment, mom, expert, wrong


NOTE = NoteContent(subjective='Private report from the client', objective='Private observations', assessment='Professional assessment', plan='Professional next steps')
PLAN = PlanContent.model_validate({'title': 'Our next steps', 'summary': 'Published client-facing summary', 'goals': ['Review progress together'],
    'tasks': [{'source_key': 'log-observation', 'title': 'Record an observation', 'description': 'Record the agreed observation.', 'category': 'Observation', 'due_label': 'Today', 'scheduled_date': None}]})


def test_client_facing_plan_requires_reviewed_english_for_publication():
    assert PLAN.ready_to_publish()
    assert PLAN.english_client_copy()
    for field, value in [
        ('title', '喂养计划'),
        ('summary', 'CozyMate will help you.'),
        ('goals', ['수유 기록']),
        ('tasks', [PLAN.tasks[0].model_copy(update={'description': 'Запишите кормление.'})]),
        ('tasks', [PLAN.tasks[0].model_copy(update={'due_label': 'مرحبا'})]),
    ]:
        legacy = PLAN.model_copy(update={field: value})
        assert legacy.ready_to_publish()  # Meaningful draft content is retained.
        assert not legacy.english_client_copy()
        assert getattr(legacy, field) == value


@postgres
def test_untranslated_plan_stays_a_draft_until_expert_reviews_english_copy():
    async def run():
        async with documented_case() as (sessions, service, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                current = service(session)
                await current.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'note', 'note')
                await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
                legacy = PLAN.model_copy(update={'summary': '请记录喂养变化'})
                saved = await current.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=legacy), 'legacy-plan', 'legacy-plan')
                assert saved.content.summary == legacy.summary
                with pytest.raises(ApiError) as error:
                    await current.publish(expert, appointment.id, VersionWrite(expected_version=saved.version), 'unreviewed', 'unreviewed')
                assert error.value.code == 'plan_language_review_required'
                assert (await current.patient_plan(mom.user_id, appointment.id)).publication is None
                reviewed = await current.save_plan(expert, appointment.id, PlanWrite(expected_version=saved.version, content=PLAN), 'reviewed', 'reviewed')
                publication = await current.publish(expert, appointment.id, VersionWrite(expected_version=reviewed.version), 'publication', 'publication')
                assert publication.summary == PLAN.summary
    asyncio.run(run())


@postgres
def test_private_notes_require_assignment_and_signature_is_immutable_with_explicit_amendments():
    async def run():
        async with documented_case() as (sessions, service, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                current = service(session)
                with pytest.raises(ApiError) as unauthorized:
                    await current.read(wrong, appointment.id)
                assert unauthorized.value.status == 404
                with pytest.raises(ApiError) as patient:
                    await current.read(mom, appointment.id)
                assert patient.value.status == 403
                saved = await current.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NoteContent()), 'draft', 'draft')
                with pytest.raises(ApiError) as incomplete:
                    await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'bad-sign', 'bad-sign')
                assert incomplete.value.code == 'note_incomplete'
                saved = await current.save_note(expert, appointment.id, NoteWrite(expected_revision=1, expected_version=saved.version, content=NOTE), 'save', 'save')
                signed = await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=saved.version), 'sign', 'sign')
                assert signed.status == 'signed' and signed.revision == 1
                with pytest.raises(ApiError) as locked:
                    await current.save_note(expert, appointment.id, NoteWrite(expected_revision=1, expected_version=signed.version, content=NOTE), 'edit-signed', 'edit-signed')
                assert locked.value.code == 'note_signed'
                new = await current.amend_note(expert, appointment.id, NoteAmendWrite(expected_revision=1, expected_version=signed.version, reason='Clarify the observation'), 'amend', 'amend')
                assert new.status == 'draft' and new.revision == 2 and new.revises_id == signed.id
                with pytest.raises(ApiError) as old_tab:
                    await current.save_note(expert, appointment.id, NoteWrite(expected_revision=1, expected_version=1, content=NOTE), 'old-tab', 'old-tab')
                assert old_tab.value.code == 'version_conflict'
                history = await current.read(expert, appointment.id)
                assert len(history.note_history) == 2
                original = await session.get(ClinicalNote, signed.id)
                assert original.status == 'signed' and original.content == NOTE.model_dump()
                public = await current.patient_plan(mom.user_id, appointment.id)
                assert public.publication is None
                assert 'Private report' not in public.model_dump_json() and 'note_history' not in public.model_dump()
    asyncio.run(run())


@postgres
def test_withdrawal_blocks_private_reads_and_replays_but_preserves_the_clients_published_plan():
    async def run():
        async with documented_case() as (sessions, service, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                current = service(session)
                note_body = NoteWrite(expected_revision=0, expected_version=0, content=NOTE)
                await current.save_note(expert, appointment.id, note_body, 'note', 'note')
                signed = await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
                await current.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'plan', 'plan')
                publication = await current.publish(expert, appointment.id, VersionWrite(expected_version=1), 'publication', 'publish')
                assert (await current.note_revision(expert, appointment.id, signed.id, 'view')).content == NOTE
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ibclc_case', version=2, active=False, policy_version='2026-09-08'))
                await session.flush()
                for call in [lambda: current.read(expert, appointment.id),
                    lambda: current.note_revision(expert, appointment.id, signed.id, 'view'),
                    lambda: current.save_note(expert, appointment.id, note_body, 'note', 'replay')]:
                    with pytest.raises(ApiError) as withdrawn:
                        await call()
                    assert withdrawn.value.code == 'case_consent_required'
                assert (await current.patient_plan(mom.user_id, appointment.id)).publication.id == publication.id
    asyncio.run(run())


@postgres
def test_publishing_requires_signed_note_and_concurrent_replay_creates_one_publication_and_task():
    async def run():
        async with documented_case() as (sessions, service, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                current = service(session)
                draft = await current.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'plan', 'plan')
                with pytest.raises(ApiError) as unsigned:
                    await current.publish(expert, appointment.id, VersionWrite(expected_version=draft.version), 'unsigned', 'unsigned')
                assert unsigned.value.code == 'signed_note_required'
                await current.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'note', 'note')
                await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
            async def publish():
                async with sessions.begin() as session:
                    return await service(session).publish(expert, appointment.id, VersionWrite(expected_version=draft.version), 'publication', 'publish')
            first, replay = await asyncio.gather(publish(), publish())
            assert first.id == replay.id and first.revision == 1
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(CarePlanPublication)) == 1
                assert await session.scalar(select(func.count()).select_from(CareTaskProgress)) == 1
                public = await service(session).patient_plan(mom.user_id, appointment.id)
                assert public.publication.id == first.id and public.publication.summary == PLAN.summary
                assert 'Private report' not in public.model_dump_json()
                with pytest.raises(ApiError) as foreign:
                    await service(session).patient_plan(wrong.user_id, appointment.id)
                assert foreign.value.status == 404
                episode = await session.get(CareEpisode, appointment.episode_id)
                assert episode.starts_at == at[0] and (episode.ends_at - episode.starts_at).days == 7 and episode.stage == 'active_care'
    asyncio.run(run())


@postgres
def test_task_feedback_is_owner_scoped_and_publication_revisions_preserve_unchanged_task_progress():
    async def run():
        async with documented_case() as (sessions, service, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                current = service(session)
                await current.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'note', 'note')
                await current.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
                await current.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'plan', 'plan')
                original = await current.publish(expert, appointment.id, VersionWrite(expected_version=1), 'publication', 'publish')
                with pytest.raises(ApiError) as foreign:
                    await current.update_task(wrong.user_id, original.id, 'log-observation', TaskProgressWrite(expected_version=1, status='completed'), 'wrong-user')
                assert foreign.value.status == 404
                completed = await current.update_task(mom.user_id, original.id, 'log-observation', TaskProgressWrite(expected_version=1, status='completed'), 'progress')
                assert completed.tasks[0].status == 'completed' and completed.tasks[0].progress_version == 2
                changed = PLAN.model_copy(update={'summary': 'Updated client-facing summary'})
                draft = await current.save_plan(expert, appointment.id, PlanWrite(expected_version=2, content=changed), 'plan-revision', 'plan-revision')
                latest = await current.publish(expert, appointment.id, VersionWrite(expected_version=draft.version), 'publication-2', 'publish-2')
                assert latest.id != original.id and latest.revision == 2 and latest.tasks[0].status == 'completed'
                with pytest.raises(ApiError) as superseded:
                    await current.update_task(mom.user_id, original.id, 'log-observation', TaskProgressWrite(expected_version=2, status='pending'), 'stale-publication')
                assert superseded.value.code == 'plan_superseded'
                old = await session.get(CarePlanPublication, original.id)
                assert old.content['summary'] == PLAN.summary
                assert (await current.patient_plan(mom.user_id, appointment.id)).publication.id == latest.id
    asyncio.run(run())
