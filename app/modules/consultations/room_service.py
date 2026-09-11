from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...infrastructure.video.provider import VideoProvider
from ..appointments.models import CareAppointment
from ..appointments.schemas import AppointmentRead, VersionWrite
from ..appointments.service import AppointmentService, utc_now
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from ..auth import CurrentUser
from ..care.models import CareEpisode
from .repository import ConsultationRepository
from .room_models import CareConsultation, CareLocationCheck, CareRoomParticipant, CareSessionConsumption
from .room_repository import RoomRepository
from .room_schemas import (ConsultationRead, EndWrite, JoinRead, LocationRead, LocationWrite, ParticipantRead,
    ParticipantRole, PresenceWrite, RoomContextRead, RoomCredentials, VideoProviderName)

TERMINAL = {"note_pending", "no_show", "failed", "cancelled", "closed"}
PRESENCE_TTL = timedelta(seconds=90)


class RoomService:
    def __init__(self, repository: RoomRepository, intakes: ConsultationRepository, appointments: AppointmentService,
        audit: AuditService, idempotency: IdempotencyService, video: VideoProvider, *,
        now: Callable[[], datetime] = utc_now, demo_early_join: bool = False) -> None:
        self.repository, self.intakes, self.appointments = repository, intakes, appointments
        self.audit, self.idempotency, self.video, self.now = audit, idempotency, video, now
        self.demo_early_join = demo_early_join

    async def _locked(self, actor: CurrentUser, appointment_id: UUID) -> tuple[CareEpisode, CareAppointment, ParticipantRole]:
        appointment = await self.repository.appointment_for_actor(actor, appointment_id)
        if appointment is None:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        episode, appointment = await self.appointments.lock_for_owner(appointment.owner_user_id, appointment.id)
        # Assignment or provider availability can change while acquiring the locks.
        if await self.repository.appointment_for_actor(actor, appointment_id) is None:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        role: ParticipantRole = "mom" if actor.user_id == appointment.owner_user_id else "ibclc"
        return episode, appointment, role

    async def context(self, actor: CurrentUser, appointment_id: UUID) -> RoomContextRead:
        _, appointment, role = await self._locked(actor, appointment_id)
        return await self._context(appointment, role)

    async def _context(self, appointment: CareAppointment, role: ParticipantRole) -> RoomContextRead:
        room = await self.repository.consultation(appointment.id)
        consents = {value.scope: value.active for value in await self.intakes.consents(appointment.episode_id)}
        location = await self.repository.location(appointment.id)
        participants = []
        for value in await self.repository.participants(room.id) if room else []:
            item = ParticipantRead.model_validate(value)
            if item.presence in {"joined", "joining"} and (item.last_seen_at is None or item.last_seen_at + PRESENCE_TTL <= self.now()):
                item = item.model_copy(update={"presence": "reconnecting"})
            participants.append(item)
        return RoomContextRead(appointment=AppointmentRead.model_validate(appointment), viewer_role=role,
            consultation=ConsultationRead.model_validate(room) if room else None, participants=participants,
            intake_ready=await self.intakes.intake(appointment.id) is not None,
            case_consent=consents.get("ibclc_case", False), video_consent=consents.get("video", False),
            location=LocationRead.model_validate(location) if location and (role == "mom" or consents.get("ibclc_case")) else None,
            opens_at=appointment.starts_at - timedelta(minutes=10), closes_at=appointment.ends_at + timedelta(minutes=15),
            server_time=self.now(), demo_early_join=self.demo_early_join, video_provider=cast(VideoProviderName, self.video.name))

    async def check_location(self, actor: CurrentUser, appointment_id: UUID, body: LocationWrite, request_id: str) -> LocationRead:
        _, appointment, role = await self._locked(actor, appointment_id)
        self._mom(role)
        self._appointment_open(appointment)
        provider = await self.appointments.repository.provider(appointment.provider_id)
        passed = provider is not None and provider.active and body.region in provider.regions
        location = CareLocationCheck(appointment_id=appointment_id, region=body.region, decision="passed" if passed else "blocked",
            expires_at=self.now() + timedelta(minutes=30), created_at=self.now())
        await self.repository.add(location)
        await self.audit.record(actor_user_id=actor.user_id, action="care.location.checked", resource_type="care_appointment",
            resource_id=str(appointment.id), request_id=request_id, details={"decision": location.decision})
        return LocationRead.model_validate(location)

    async def prepare(self, actor: CurrentUser, appointment_id: UUID, request_id: str) -> RoomContextRead:
        episode, appointment, role = await self._locked(actor, appointment_id)
        room = await self.repository.consultation(appointment_id, lock=True)
        await self._can_enter(episode, appointment, room, role, preparing=True)
        if room is None:
            room = await self._new_room(appointment, create_media=True)
            await self._audit(actor, room, "prepared", request_id)
        elif room.room_status == "closed":
            room.room_generation += 1
            room.room_name = f"care-{room.id.hex}-{room.room_generation}"
            room.room_status = "creating"
            room.version += 1
            for participant in await self.repository.participants(room.id):
                participant.connection_id, participant.presence = None, "not_joined"
                participant.connection_version += 1
                participant.last_seen_at = None
            await self.repository.enqueue(room, "create", self.now())
            await self._audit(actor, room, "reopened", request_id)
        await self.repository.flush()
        return await self._context(appointment, role)

    async def join(self, actor: CurrentUser, appointment_id: UUID, key: str, request_id: str) -> JoinRead:
        episode, appointment, role = await self._locked(actor, appointment_id)
        room = await self.repository.consultation(appointment_id, lock=True)
        await self._can_enter(episode, appointment, room, role)
        if room is None or room.room_status != "ready":
            raise ApiError(code="room_not_ready", message="The video room is still being prepared.", status=409)
        participant = await self.repository.participant(room.id, role)
        assert participant is not None
        decision = await self.idempotency.reserve(actor_user_id=actor.user_id, scope=f"care.room.join:{appointment_id}", key=key,
            request_hash=request_hash({"appointment_id": str(appointment_id)}), expires_at=self.now() + timedelta(days=1))
        if decision.status == "replay":
            connection_id = parse_idempotency_response_ref(decision.record.response_ref)
            if connection_id != participant.connection_id:
                raise ApiError(code="connection_replaced", message="This connection was replaced. Join again.", status=409)
            if participant.presence == "left":
                raise ApiError(code="connection_closed", message="You left this connection. Join again.", status=409)
        else:
            participant.connection_id = uuid4()
            participant.connection_version += 1
            participant.presence = "joining"
            participant.last_seen_at, participant.left_at = self.now(), None
            await self.repository.flush()
            await self.idempotency.mark_completed(record=decision.record, response_ref=str(participant.connection_id))
            await self._audit(actor, room, "joining", request_id)
        credentials = self.video.credentials(room_name=room.room_name, participant_identity=str(actor.user_id))
        assert participant.connection_id is not None
        return JoinRead(context=await self._context(appointment, role), connection_id=participant.connection_id,
            credentials=RoomCredentials(server_url=credentials.server_url, token=credentials.token, expires_at=credentials.expires_at))

    async def presence(self, actor: CurrentUser, appointment_id: UUID, body: PresenceWrite, request_id: str) -> RoomContextRead:
        episode, appointment, role = await self._locked(actor, appointment_id)
        room = await self._room(appointment_id)
        participant = await self.repository.participant(room.id, role)
        if participant is None or participant.connection_id != body.connection_id:
            raise ApiError(code="connection_replaced", message="This connection was replaced.", status=409)
        if body.presence != "left":
            if participant.presence == "left":
                raise ApiError(code="connection_closed", message="Join again before reconnecting.", status=409)
            await self._can_enter(episode, appointment, room, role, check_location=False)
            if room.room_status != "ready":
                raise ApiError(code="room_not_ready", message="The video room is unavailable.", status=409)
            if body.presence == "joined" and self.video.name == "livekit":
                if str(actor.user_id) not in await self._media_participants(room):
                    raise ApiError(code="media_not_connected", message="The video connection is not ready yet.", status=409)
        if body.presence == "joined" and participant.joined_at is None:
            participant.joined_at = self.now()
        changed = participant.presence != body.presence
        participant.presence, participant.last_seen_at = body.presence, self.now()
        if body.presence == "left":
            participant.left_at = self.now()
        await self.repository.flush()
        if changed:
            await self._audit(actor, room, body.presence, request_id)
        return await self._context(appointment, role)

    async def start(self, actor: CurrentUser, appointment_id: UUID, body: VersionWrite, request_id: str) -> RoomContextRead:
        episode, appointment, role = await self._locked(actor, appointment_id)
        self._expert(role)
        room = await self._room(appointment_id)
        if room.status == "in_progress":
            return await self._context(appointment, role)
        self._version(room, body.expected_version)
        await self._can_enter(episode, appointment, room, role)
        if room.status != "waiting_room" or room.room_status != "ready":
            raise ApiError(code="consultation_state", message="This consultation cannot be started.", status=409)
        await self._location_ready(appointment)
        participants = await self.repository.participants(room.id)
        if len(participants) != 2 or any(value.presence != "joined" or value.last_seen_at is None or value.last_seen_at + PRESENCE_TTL <= self.now() for value in participants):
            raise ApiError(code="participants_required", message="Both participants must be connected before starting.", status=409)
        if self.video.name == "livekit":
            present = await self._media_participants(room)
            if not {str(appointment.owner_user_id), str(appointment.provider_id)} <= present:
                raise ApiError(code="participants_required", message="Both participants must be connected before starting.", status=409)
        intake = await self.intakes.intake(appointment_id)
        if intake is None:
            raise ApiError(code="intake_required", message="Complete the intake before starting.", status=409)
        room.status, room.started_at, room.intake_revision_id = "in_progress", self.now(), intake.id
        room.version += 1
        appointment.status = "in_progress"
        appointment.version += 1
        await self.repository.flush()
        await self._audit(actor, room, "started", request_id)
        from ..care.events import record_care_event
        await record_care_event(self.repository.session, episode_id=episode.id, kind="consultation_started",
            aggregate_id=room.id, aggregate_version=room.version, appointment_id=appointment.id,
            actor_user_id=actor.user_id, recipient_id=None, occurred_at=self.now())
        return await self._context(appointment, role)

    async def end(self, actor: CurrentUser, appointment_id: UUID, body: EndWrite, request_id: str) -> RoomContextRead:
        episode, appointment, role = await self._locked(actor, appointment_id)
        self._expert(role)
        room = await self.repository.consultation(appointment_id, lock=True)
        if room and room.status in TERMINAL:
            if room.end_reason == body.reason:
                return await self._context(appointment, role)
            raise ApiError(code="consultation_ended", message="This consultation has already ended.", status=409)
        self._appointment_open(appointment)
        if body.reason == "user_no_show":
            if self.now() < appointment.starts_at + timedelta(minutes=10):
                raise ApiError(code="no_show_too_early", message="Wait until ten minutes after the scheduled start.", status=409)
            participant = await self.repository.participant(room.id, "mom") if room else None
            if room and (room.started_at is not None or (participant and (participant.joined_at is not None or (
                participant.presence == "joining" and participant.last_seen_at and participant.last_seen_at + PRESENCE_TTL > self.now())))):
                raise ApiError(code="no_show_not_allowed", message="The user has joined this consultation.", status=409)
            if room and self.video.name == "livekit" and str(appointment.owner_user_id) in await self._media_participants(room):
                raise ApiError(code="no_show_not_allowed", message="The user is connected to the room.", status=409)
        elif body.reason == "completed" and (room is None or room.status != "in_progress"):
            raise ApiError(code="consultation_not_started", message="Start the consultation before completing it.", status=409)
        elif room is None:
            raise ApiError(code="room_not_found", message="No consultation room exists.", status=409)
        if room is None:
            if body.expected_version != 0:
                raise ApiError(code="version_conflict", message="Reload the consultation before continuing.", status=409)
            room = await self._new_room(appointment, create_media=False)
        else:
            self._version(room, body.expected_version)
        if body.reason == "completed":
            if episode.remaining_sessions < 1:
                raise ApiError(code="service_exhausted", message="No consultation entitlement remains.", status=409)
            await self.repository.add(CareSessionConsumption(consultation_id=room.id, episode_id=episode.id, consumed_at=self.now()))
            episode.remaining_sessions -= 1
            episode.version += 1
        room.status = {"completed": "note_pending", "safety_escalation": "note_pending", "technical_failure": "failed", "user_no_show": "no_show"}[body.reason]
        room.ended_at, room.end_reason = self.now(), body.reason
        room.version += 1
        appointment.status = "completed"
        appointment.version += 1
        if room.room_status != "closed":
            room.room_status = "closing"
            await self.repository.enqueue(room, "close", self.now())
        await self.repository.flush()
        await self._audit(actor, room, "ended", request_id, reason=body.reason)
        from ..care.events import record_care_event
        from ..care.event_models import CareEventKind
        event_kind: CareEventKind = ('consultation_user_no_show' if body.reason == 'user_no_show' else
            'consultation_technical_failure' if body.reason == 'technical_failure' else 'consultation_completed')
        await record_care_event(self.repository.session, episode_id=episode.id, kind=event_kind,
            aggregate_id=room.id, aggregate_version=room.version, appointment_id=appointment.id,
            actor_user_id=actor.user_id, recipient_id=appointment.provider_id, occurred_at=self.now())
        if body.reason == "completed":
            await record_care_event(self.repository.session, episode_id=episode.id, kind="service_progress_changed",
                aggregate_id=episode.id, aggregate_version=episode.version, actor_user_id=actor.user_id, recipient_id=None, occurred_at=self.now())
        return await self._context(appointment, role)

    async def _new_room(self, appointment: CareAppointment, *, create_media: bool) -> CareConsultation:
        identifier = uuid4()
        room = CareConsultation(id=identifier, appointment_id=appointment.id, episode_id=appointment.episode_id,
            video_provider=self.video.name, room_name=f"care-{identifier.hex}-1", room_status="creating" if create_media else "closed")
        await self.repository.add(room)
        for role, user_id in [("mom", appointment.owner_user_id), ("ibclc", appointment.provider_id)]:
            await self.repository.add(CareRoomParticipant(consultation_id=room.id, role=role, actor_user_id=user_id))
        if create_media:
            await self.repository.enqueue(room, "create", self.now())
        return room

    async def _can_enter(self, episode: CareEpisode, appointment: CareAppointment, room: CareConsultation | None, role: ParticipantRole, *, preparing: bool = False, check_location: bool = True) -> None:
        self._appointment_open(appointment)
        if room and room.status in TERMINAL:
            raise ApiError(code="consultation_ended", message="This consultation has ended.", status=409)
        if episode.status != "active" or episode.remaining_sessions < 1:
            raise ApiError(code="service_unavailable", message="This consultation service is unavailable.", status=409)
        if self.video.name == "disabled" or (room and room.video_provider != self.video.name):
            raise ApiError(code="video_unavailable", message="Video consultation is currently unavailable.", status=503)
        if not (room and room.status == "in_progress"):
            if self.now() < appointment.starts_at - timedelta(minutes=10) and not self.demo_early_join:
                raise ApiError(code="room_not_open", message="The room opens ten minutes before the appointment.", status=409)
            if self.now() >= appointment.ends_at + timedelta(minutes=15):
                raise ApiError(code="room_window_closed", message="The appointment entry window has closed.", status=409)
        if role == "mom" and await self.intakes.intake(appointment.id) is None:
            raise ApiError(code="intake_required", message="Complete the intake before joining.", status=409)
        # Experts may prepare a waiting room while the user completes preparation.
        if not (preparing and role == "ibclc"):
            consents = {value.scope: value.active for value in await self.intakes.consents(episode.id)}
            if not consents.get("ibclc_case") or not consents.get("video"):
                raise ApiError(code="consent_required", message="Current case-sharing and video consent are required.", status=403)
        if role == "mom" and check_location:
            await self._location_ready(appointment)

    async def _location_ready(self, appointment: CareAppointment) -> None:
        location = await self.repository.location(appointment.id)
        provider = await self.appointments.repository.provider(appointment.provider_id)
        if location is None or location.decision != "passed" or location.expires_at <= self.now() or provider is None or not provider.active or location.region not in provider.regions:
            raise ApiError(code="location_required", message="Confirm your current state before joining.", status=409)

    async def _media_participants(self, room: CareConsultation) -> set[str]:
        try:
            return await self.video.participants(room.room_name)
        except Exception as error:
            raise ApiError(code="video_unavailable", message="The video connection could not be verified. Retry shortly.", status=503) from error

    async def _room(self, appointment_id: UUID) -> CareConsultation:
        room = await self.repository.consultation(appointment_id, lock=True)
        if room is None:
            raise ApiError(code="room_not_found", message="Prepare the room before joining.", status=409)
        return room

    @staticmethod
    def _appointment_open(appointment: CareAppointment) -> None:
        if appointment.status not in {"confirmed", "in_progress"}:
            raise ApiError(code="appointment_state", message="This appointment is not open for consultation.", status=409)

    @staticmethod
    def _version(room: CareConsultation, expected: int) -> None:
        if room.version != expected:
            raise ApiError(code="version_conflict", message="The consultation changed. Refresh before continuing.", status=409)

    @staticmethod
    def _mom(role: ParticipantRole) -> None:
        if role != "mom":
            raise ApiError(code="forbidden", message="Only the user can confirm their current location.", status=403)

    @staticmethod
    def _expert(role: ParticipantRole) -> None:
        if role != "ibclc":
            raise ApiError(code="forbidden", message="Only the assigned IBCLC can start or end this consultation.", status=403)

    async def _audit(self, actor: CurrentUser, room: CareConsultation, action: str, request_id: str, *, reason: str | None = None) -> None:
        await self.audit.record(actor_user_id=actor.user_id, action=f"care.consultation.{action}", resource_type="care_consultation",
            resource_id=str(room.id), request_id=request_id, details={"version": room.version, **({"reason": reason} if reason else {})})
