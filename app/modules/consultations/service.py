from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from ...core.errors import ApiError
from ..appointments.service import AppointmentService, utc_now
from ..audit.service import AuditService
from ..auth import CurrentUser
from .models import CareConsentRevision, CareIntakeRevision
from .repository import ConsultationRepository
from .room_repository import RoomRepository
from ..baby.profile_schemas import BabyProfileRead
from .schemas import (ConsentRead, ConsentWrite,  IntakeContent, IntakeContextRead, IntakeRead, IntakeWrite)


class ConsultationService:
    def __init__(self, repository: ConsultationRepository, appointments: AppointmentService, audit: AuditService,
        *, now: Callable[[], datetime] = utc_now) -> None:
        self.repository, self.appointments, self.audit, self.now = repository, appointments, audit, now

    async def intake_context(self, owner: UUID, appointment_id: UUID) -> IntakeContextRead:
        appointment = await self.appointments.read(owner, appointment_id)
        current = await self.repository.intake(appointment.id)
        previous = await self.repository.previous_intake(appointment.episode_id, appointment.id) if current is None else None
        return IntakeContextRead(appointment=appointment, intake=IntakeRead.model_validate(current) if current else None,
            previous_intake=IntakeRead.model_validate(previous) if previous else None,
            consents=[ConsentRead.model_validate(value) for value in await self.repository.consents(appointment.episode_id)],
            babies=[BabyProfileRead.model_validate(value) for value in await self.repository.babies(owner)],
            delivery_date=await self.repository.delivery_date(owner))

    async def save_intake(self, owner: UUID, appointment_id: UUID, body: IntakeWrite, request_id: str) -> IntakeRead:
        episode, appointment = await self.appointments.lock_for_owner(owner, appointment_id)
        if appointment.status not in {"confirmed", "in_progress"}:
            raise ApiError(code="appointment_state", message="Confirm the appointment before submitting its intake.", status=409)
        current = await self.repository.intake(appointment_id)
        content = body.content()
        version = current.version if current else 0
        if current and version == body.expected_version + 1 and self._content(current) == content:
            # A transport replay must not grant consent again after a later withdrawal.
            return IntakeRead.model_validate(current)
        if version != body.expected_version:
            raise ApiError(code="version_conflict", message="This intake changed. Reload before editing.", status=409)
        babies = await self.repository.babies(owner)
        if not any(value.id == body.profile.baby_id for value in babies):
            raise ApiError(code="not_found", message="Baby profile not found.", status=404)
        if episode.baby_id is not None and episode.baby_id != body.profile.baby_id:
            raise ApiError(code="service_baby_mismatch", message="This service is associated with a different baby.", status=409)
        today = self.now().astimezone(ZoneInfo(appointment.timezone)).date()
        if body.profile.baby_birth_date > today or body.profile.delivery_date > today:
            raise ApiError(code="invalid_date", message="Birth and delivery dates cannot be in the future.", status=422)
        provider = await self.appointments.repository.provider(appointment.provider_id)
        if provider is None or body.profile.region not in provider.regions:
            raise ApiError(code="region_unavailable", message="Your specialist does not currently cover this region.", status=409)
        await self._consent_locked(owner, episode.id, ConsentWrite(expected_version=body.expected_consent_version, scope="ibclc_case", active=True,
            policy_version=body.consent_policy_version), request_id, replay=False)
        revision = CareIntakeRevision(appointment_id=appointment_id, episode_id=episode.id, version=version + 1,
            symptoms=list(content.symptoms), feeding_goal=content.feeding_goal, support_needed=content.support_needed,
            profile=content.profile.model_dump(mode="json"), submitted_at=self.now())
        episode.baby_id = body.profile.baby_id
        appointment.intake_version = revision.version
        appointment.version += 1
        episode.version += 1
        await self.repository.add(revision)
        from ..care.events import record_care_event
        await record_care_event(self.repository.session, episode_id=episode.id, kind='intake_submitted',
            aggregate_id=appointment.id, aggregate_version=revision.version, appointment_id=appointment.id,
            actor_user_id=owner, recipient_id=appointment.provider_id, occurred_at=self.now())
        await self.audit.record(actor_user_id=owner, action="care.intake.submitted", resource_type="care_intake",
            resource_id=str(revision.id), request_id=request_id, details={"version": revision.version})
        return IntakeRead.model_validate(revision)

    async def consents(self, owner: UUID, episode_id: UUID) -> list[ConsentRead]:
        await self.appointments.context(owner, episode_id)
        return [ConsentRead.model_validate(value) for value in await self.repository.consents(episode_id)]

    async def set_consent(self, owner: UUID, episode_id: UUID, body: ConsentWrite, request_id: str) -> ConsentRead:
        await self.appointments.repository.lock_owner(owner)
        episode = await self.appointments.repository.episode(owner, episode_id, lock=True)
        if episode is None:
            raise ApiError(code="not_found", message="Service not found.", status=404)
        return await self._consent_locked(owner, episode_id, body, request_id)

    async def _consent_locked(self, owner: UUID, episode_id: UUID, body: ConsentWrite, request_id: str, *, replay: bool = True) -> ConsentRead:
        current = await self.repository.consent(episode_id, body.scope)
        version = current.version if current else 0
        same = current is not None and current.active == body.active and current.policy_version == body.policy_version
        if replay and same and version == body.expected_version + 1:
            return ConsentRead.model_validate(current)
        if version != body.expected_version:
            raise ApiError(code="consent_conflict", message="Your consent changed. Review it before continuing.", status=409)
        if same:
            return ConsentRead.model_validate(current)
        result = CareConsentRevision(episode_id=episode_id, scope=body.scope, version=version + 1, active=body.active,
            policy_version=body.policy_version, recorded_at=self.now())
        await self.repository.add(result)
        if not body.active and body.scope in {"ibclc_case", "video"}:
            await RoomRepository(self.repository.session).close_for_consent(episode_id, self.now())
        if not body.active and body.scope == 'ibclc_case':
            from ..care.events import record_care_event
            episode = await self.appointments.repository.episode(owner, episode_id)
            assert episode is not None
            await record_care_event(self.repository.session, episode_id=episode_id, kind='case_consent_revoked',
                aggregate_id=result.id, aggregate_version=result.version, actor_user_id=owner,
                recipient_id=episode.assigned_ibclc_id, occurred_at=self.now())
        await self.audit.record(actor_user_id=owner, action="care.consent.granted" if body.active else "care.consent.revoked", resource_type="care_consent",
            resource_id=str(result.id), request_id=request_id, details={"scope": body.scope, "version": result.version})
        return ConsentRead.model_validate(result)

    async def expert_intake(self, actor: CurrentUser, appointment_id: UUID, request_id: str = "") -> IntakeRead:
        if "ibclc" not in actor.roles:
            raise ApiError(code="forbidden", message="An IBCLC account is required.", status=403)
        appointment = await self.repository.assigned_appointment(actor.user_id, appointment_id)
        if appointment is None or appointment.status not in {"confirmed", "in_progress", "completed"}:
            raise ApiError(code="not_found", message="Assigned appointment not found.", status=404)
        consent = await self.repository.consent(appointment.episode_id, "ibclc_case")
        if consent is None or not consent.active:
            raise ApiError(code="consent_required", message="Permission to view this intake is inactive.", status=403)
        current = await self.repository.intake(appointment_id)
        if current is None:
            raise ApiError(code="not_found", message="The intake has not been submitted.", status=404)
        await self.audit.record(actor_user_id=actor.user_id, action="care.intake.viewed", resource_type="care_intake",
            resource_id=str(current.id), request_id=request_id, details={"version": current.version})
        return IntakeRead.model_validate(current)

    @staticmethod
    def _content(value: CareIntakeRevision) -> IntakeContent:
        return IntakeContent.model_validate({"symptoms": value.symptoms, "feeding_goal": value.feeding_goal, "support_needed": value.support_needed, "profile": value.profile})
