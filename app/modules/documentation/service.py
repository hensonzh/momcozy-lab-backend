from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel

from ...core.errors import ApiError
from ..appointments.models import CareAppointment
from ..appointments.schemas import AppointmentRead, VersionWrite
from ..appointments.service import AppointmentService, utc_now
from ..audit.service import AuditService, IdempotencyDecision, IdempotencyService, parse_idempotency_response_ref, request_hash
from ..auth import CurrentUser
from ..care.models import CareEpisode, CareOrder
from ..care.schemas import CareEpisodeRead
from ..consultations.repository import ConsultationRepository
from ..consultations.room_models import CareConsultation
from ..consultations.room_repository import RoomRepository
from ..consultations.room_schemas import ConsultationRead
from .models import CarePlanDraft, CarePlanPublication, CareTaskProgress, ClinicalNote
from .repository import DocumentationRepository
from .schemas import (DocumentationRead, NoteAmendWrite, NoteRead, NoteRevisionRead, NoteVersionWrite, NoteWrite,
    PatientPlanRead, PlanContent, PlanDraftRead, PlanPublicationRead, PlanWrite, PublishedTaskRead, TaskProgressWrite)


class DocumentationService:
    def __init__(self, repository: DocumentationRepository, appointments: AppointmentService,
        audit: AuditService, idempotency: IdempotencyService, *, now: Callable[[], datetime] = utc_now) -> None:
        self.repository, self.appointments = repository, appointments
        self.audit, self.idempotency, self.now = audit, idempotency, now
        self.rooms = RoomRepository(repository.session)
        self.intakes = ConsultationRepository(repository.session)

    async def _expert_context(self, actor: CurrentUser, appointment_id: UUID) -> tuple[CareEpisode, CareAppointment, CareConsultation | None]:
        if "ibclc" not in actor.roles:
            raise ApiError(code="permission_denied", message="Only the assigned specialist can access clinical documentation.", status=403)
        appointment = await self.rooms.appointment_for_actor(actor, appointment_id)
        if appointment is None or appointment.provider_id != actor.user_id or appointment.owner_user_id == actor.user_id:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        episode, appointment = await self.appointments.lock_for_owner(appointment.owner_user_id, appointment.id)
        # Revalidate assignment after the shared owner → episode → provider → appointment locks.
        if await self.rooms.appointment_for_actor(actor, appointment_id) is None:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        consent = await self.intakes.consent(episode.id, "ibclc_case")
        if consent is None or not consent.active:
            raise ApiError(code="case_consent_required", message="The client has not granted access to this care case.", status=403)
        return episode, appointment, await self.rooms.consultation(appointment_id, lock=True)

    @staticmethod
    def _documentable(room: CareConsultation | None) -> CareConsultation:
        if room is None or room.status not in {"note_pending", "closed"}:
            raise ApiError(code="documentation_not_ready", message="Complete the consultation before writing clinical documentation.", status=409)
        return room

    async def read(self, actor: CurrentUser, appointment_id: UUID, request_id: str = "") -> DocumentationRead:
        _, appointment, room = await self._expert_context(actor, appointment_id)
        note = await self.repository.current_note(room.id) if room else None
        plan = await self.repository.plan(room.id) if room else None
        publication = await self.repository.latest_publication(plan.id) if plan else None
        await self._audit(actor.user_id, appointment.id, "viewed", request_id)
        return DocumentationRead(appointment=AppointmentRead.model_validate(appointment),
            consultation=ConsultationRead.model_validate(room) if room else None,
            note=NoteRead.model_validate(note) if note else None,
            note_history=[NoteRevisionRead.model_validate(value) for value in await self.repository.note_history(room.id)] if room else [],
            plan=PlanDraftRead.model_validate(plan) if plan else None,
            publication=await self._publication(publication, room.id) if publication and room else None)

    async def note_revision(self, actor: CurrentUser, appointment_id: UUID, note_id: UUID, request_id: str) -> NoteRead:
        _, appointment, room = await self._expert_context(actor, appointment_id)
        note = await self.repository.note(room.id, note_id) if room else None
        if note is None:
            raise ApiError(code="not_found", message="Clinical note not found.", status=404)
        await self._audit(actor.user_id, appointment.id, "note_viewed", request_id, revision=note.revision)
        return NoteRead.model_validate(note)

    async def save_note(self, actor: CurrentUser, appointment_id: UUID, body: NoteWrite, key: str, request_id: str) -> NoteRead:
        _, appointment, current = await self._expert_context(actor, appointment_id)
        room = self._documentable(current)
        decision = await self._reserve(actor, appointment_id, "note.save", body, key)
        if decision.status == "replay":
            return await self._replay_note(room, decision)
        note = await self.repository.current_note(room.id)
        self._note_version(note, body.expected_revision, body.expected_version)
        if note and note.status == "signed":
            raise ApiError(code="note_signed", message="Signed notes are read-only. Create an amendment with a reason.", status=409)
        if note is None:
            note = ClinicalNote(consultation_id=room.id, author_id=actor.user_id, content=body.content.model_dump(mode="json"), updated_at=self.now())
            await self.repository.add(note)
        else:
            note.content, note.updated_at = body.content.model_dump(mode="json"), self.now()
            note.version += 1
        await self._complete(decision, note.id)
        await self._audit(actor.user_id, appointment.id, "note_saved", request_id, revision=note.revision, version=note.version)
        return NoteRead.model_validate(note)

    async def sign_note(self, actor: CurrentUser, appointment_id: UUID, body: NoteVersionWrite, key: str, request_id: str) -> NoteRead:
        _, appointment, current = await self._expert_context(actor, appointment_id)
        room = self._documentable(current)
        decision = await self._reserve(actor, appointment_id, "note.sign", body, key)
        if decision.status == "replay":
            return await self._replay_note(room, decision)
        note = await self.repository.current_note(room.id)
        self._note_version(note, body.expected_revision, body.expected_version)
        assert note is not None
        if note.status == "signed":
            raise ApiError(code="note_signed", message="This note has already been signed.", status=409)
        if not all(note.content.get(field, "").strip() for field in ("subjective", "objective", "assessment", "plan")):
            raise ApiError(code="note_incomplete", message="Complete all four SOAP sections before signing.", status=422)
        note.status, note.signed_at, note.updated_at = "signed", self.now(), self.now()
        note.version += 1
        await self._complete(decision, note.id)
        await self._audit(actor.user_id, appointment.id, "note_signed", request_id, revision=note.revision, version=note.version)
        return NoteRead.model_validate(note)

    async def amend_note(self, actor: CurrentUser, appointment_id: UUID, body: NoteAmendWrite, key: str, request_id: str) -> NoteRead:
        _, appointment, current = await self._expert_context(actor, appointment_id)
        room = self._documentable(current)
        decision = await self._reserve(actor, appointment_id, "note.amend", body, key)
        if decision.status == "replay":
            return await self._replay_note(room, decision)
        previous = await self.repository.current_note(room.id)
        self._note_version(previous, body.expected_revision, body.expected_version)
        if previous is None or previous.status != "signed":
            raise ApiError(code="signed_note_required", message="Sign the current revision before creating another amendment.", status=409)
        note = ClinicalNote(consultation_id=room.id, author_id=actor.user_id, revises_id=previous.id,
            amendment_reason=body.reason, revision=previous.revision + 1, content=dict(previous.content), updated_at=self.now())
        await self.repository.add(note)
        await self._complete(decision, note.id)
        await self._audit(actor.user_id, appointment.id, "note_amended", request_id, revision=note.revision)
        return NoteRead.model_validate(note)

    async def save_plan(self, actor: CurrentUser, appointment_id: UUID, body: PlanWrite, key: str, request_id: str) -> PlanDraftRead:
        _, appointment, current = await self._expert_context(actor, appointment_id)
        room = self._documentable(current)
        decision = await self._reserve(actor, appointment_id, "plan.save", body, key)
        plan = await self.repository.plan(room.id)
        if decision.status == "replay":
            if plan is None or plan.id != parse_idempotency_response_ref(decision.record.response_ref):
                raise ApiError(code="not_found", message="Care plan not found.", status=404)
            return PlanDraftRead.model_validate(plan)
        self._version(plan.version if plan else 0, body.expected_version)
        if plan is None:
            plan = CarePlanDraft(consultation_id=room.id, author_id=actor.user_id, content=body.content.model_dump(mode="json"), updated_at=self.now())
            await self.repository.add(plan)
        else:
            plan.content, plan.updated_at = body.content.model_dump(mode="json"), self.now()
            plan.version += 1
        await self._complete(decision, plan.id)
        await self._audit(actor.user_id, appointment.id, "plan_saved", request_id, version=plan.version)
        return PlanDraftRead.model_validate(plan)

    async def publish(self, actor: CurrentUser, appointment_id: UUID, body: VersionWrite, key: str, request_id: str) -> PlanPublicationRead:
        episode, appointment, current = await self._expert_context(actor, appointment_id)
        room = self._documentable(current)
        decision = await self._reserve(actor, appointment_id, "plan.publish", body, key)
        plan = await self.repository.plan(room.id)
        if decision.status == "replay":
            publication = await self.repository.publication(parse_idempotency_response_ref(decision.record.response_ref))
            if publication is None or plan is None or publication.plan_id != plan.id:
                raise ApiError(code="not_found", message="Published plan not found.", status=404)
            return await self._publication(publication, room.id)
        note = await self.repository.current_note(room.id)
        if note is None or note.status != "signed":
            raise ApiError(code="signed_note_required", message="Sign the current clinical note before publishing the care plan.", status=409)
        if plan is None:
            raise ApiError(code="plan_required", message="Save a care plan before publishing.", status=409)
        self._version(plan.version, body.expected_version)
        content = PlanContent.model_validate(plan.content)
        if not content.ready_to_publish():
            raise ApiError(code="plan_incomplete", message="Complete the summary, goals and task details before publishing.", status=422)
        if not content.english_client_copy():
            raise ApiError(code="plan_language_review_required", message="Review the client-facing care plan in English before publishing.", status=422)
        provider = await self.appointments.repository.provider(actor.user_id)
        assert provider is not None
        previous = await self.repository.latest_publication(plan.id)
        publication = CarePlanPublication(plan_id=plan.id, signed_note_id=note.id, publisher_id=actor.user_id,
            publisher_name=provider.display_name, revision=plan.published_revision + 1,
            content=content.model_dump(mode="json"), published_at=self.now())
        await self.repository.add(publication)
        previous_tasks = {task["source_key"]: task for task in previous.content["tasks"]} if previous else {}
        previous_progress = {value.source_key: value for value in await self.repository.progress(previous.id)} if previous else {}
        for task in content.tasks:
            old = previous_progress.get(task.source_key)
            unchanged = task.model_dump(mode="json") == previous_tasks.get(task.source_key)
            await self.repository.add(CareTaskProgress(publication_id=publication.id, source_key=task.source_key,
                status=old.status if old and unchanged else "pending", updated_at=old.updated_at if old and unchanged else self.now()))
        plan.published_revision, plan.updated_at = publication.revision, self.now()
        plan.version += 1
        if room.status != "closed":
            room.status = "closed"
            room.version += 1
        if episode.starts_at is None:
            order = await self.repository.session.get(CareOrder, episode.order_id)
            assert order is not None
            episode.starts_at, episode.ends_at = self.now(), self.now() + timedelta(days=order.duration_days)
            episode.stage = "active_care"
            episode.version += 1
            from ..care.events import record_care_event
            await self.repository.flush()
            await record_care_event(self.repository.session, episode_id=episode.id, kind="service_progress_changed",
                aggregate_id=episode.id, aggregate_version=episode.version, actor_user_id=actor.user_id,
                recipient_id=None, occurred_at=self.now())
        await self._complete(decision, publication.id)
        await self._audit(actor.user_id, appointment.id, "plan_published", request_id, revision=publication.revision)
        from ..care.events import record_care_event
        await record_care_event(self.repository.session, episode_id=episode.id, kind='plan_published',
            aggregate_id=publication.id, aggregate_version=publication.revision, appointment_id=appointment.id,
            actor_user_id=actor.user_id, recipient_id=None, occurred_at=self.now())
        return await self._publication(publication, room.id)

    async def patient_plan(self, owner: UUID, appointment_id: UUID) -> PatientPlanRead:
        episode, appointment = await self.appointments.lock_for_owner(owner, appointment_id)
        room = await self.rooms.consultation(appointment_id)
        plan = await self.repository.plan(room.id) if room else None
        publication = await self.repository.latest_publication(plan.id) if plan else None
        return PatientPlanRead(episode=CareEpisodeRead.model_validate(episode), appointment=AppointmentRead.model_validate(appointment),
            consultation=ConsultationRead.model_validate(room) if room else None,
            publication=await self._publication(publication, room.id) if publication and room else None)

    async def update_task(self, owner: UUID, publication_id: UUID, source_key: str, body: TaskProgressWrite, request_id: str) -> PlanPublicationRead:
        appointment_id = await self.repository.publication_appointment(publication_id)
        if appointment_id is None:
            raise ApiError(code="not_found", message="Published plan not found.", status=404)
        await self.appointments.lock_for_owner(owner, appointment_id)
        publication = await self.repository.publication(publication_id)
        assert publication is not None
        latest = await self.repository.latest_publication(publication.plan_id)
        if latest is None or latest.id != publication.id:
            raise ApiError(code="plan_superseded", message="A newer care plan is available. Refresh before updating tasks.", status=409)
        task = await self.repository.task(publication.id, source_key)
        if task is None:
            raise ApiError(code="not_found", message="Care task not found.", status=404)
        room = await self.rooms.consultation(appointment_id)
        assert room is not None
        if task.version == body.expected_version + 1 and task.status == body.status:
            return await self._publication(publication, room.id)
        self._version(task.version, body.expected_version)
        task.status, task.updated_at = body.status, self.now()
        task.version += 1
        await self.repository.flush()
        await self._audit(owner, appointment_id, "task_updated", request_id, version=task.version)
        return await self._publication(publication, room.id)

    async def _publication(self, value: CarePlanPublication, consultation_id: UUID) -> PlanPublicationRead:
        progress = {item.source_key: item for item in await self.repository.progress(value.id)}
        content = PlanContent.model_validate(value.content)
        return PlanPublicationRead(id=value.id, plan_id=value.plan_id, consultation_id=consultation_id,
            revision=value.revision, title=content.title, summary=content.summary, goals=content.goals,
            tasks=[PublishedTaskRead.model_validate({**task.model_dump(), "status": progress[task.source_key].status,
                "progress_version": progress[task.source_key].version}) for task in content.tasks],
            publisher_name=value.publisher_name, published_at=value.published_at)

    async def _reserve(self, actor: CurrentUser, appointment_id: UUID, action: str, body: BaseModel, key: str) -> IdempotencyDecision:
        return await self.idempotency.reserve(actor_user_id=actor.user_id, scope=f"care.documentation.{action}:{appointment_id}", key=key,
            request_hash=request_hash(body.model_dump(mode="json")), expires_at=self.now() + timedelta(days=1))

    async def _complete(self, decision: IdempotencyDecision, reference: UUID) -> None:
        await self.repository.flush()
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(reference))

    async def _replay_note(self, room: CareConsultation, decision: IdempotencyDecision) -> NoteRead:
        note = await self.repository.note(room.id, parse_idempotency_response_ref(decision.record.response_ref))
        if note is None:
            raise ApiError(code="not_found", message="Clinical note not found.", status=404)
        return NoteRead.model_validate(note)

    @classmethod
    def _note_version(cls, note: ClinicalNote | None, revision: int, version: int) -> None:
        cls._version(note.revision if note else 0, revision)
        cls._version(note.version if note else 0, version)

    @staticmethod
    def _version(actual: int, expected: int) -> None:
        if actual != expected:
            raise ApiError(code="version_conflict", message="This document changed. Refresh before continuing.", status=409)

    async def _audit(self, actor: UUID, appointment: UUID, action: str, request_id: str, **details: int) -> None:
        await self.audit.record(actor_user_id=actor, action=f"care.documentation.{action}", resource_type="care_appointment",
            resource_id=str(appointment), request_id=request_id, details=details)
