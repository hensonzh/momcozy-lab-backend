from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from typing import Literal
from zoneinfo import ZoneInfo

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from ..care.models import CareEpisode, CareProvider
from ..care.schemas import CareEpisodeRead, CareProviderRead
from .availability import slots_for_day
from .models import BookingEligibility, CareAppointment
from .repository import AppointmentRepository
from .schemas import (AppointmentRead, AvailabilityRead, AvailabilitySlot, BookingContextRead, BookingEligibilityRead,
    BookingPrecheckWrite, HoldWrite, VersionWrite)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AppointmentService:
    def __init__(self, repository: AppointmentRepository, audit: AuditService, idempotency: IdempotencyService,
        *, sandbox_enabled: bool, now: Callable[[], datetime] = utc_now) -> None:
        self.repository, self.audit, self.idempotency = repository, audit, idempotency
        self.sandbox_enabled, self.now = sandbox_enabled, now

    async def context(self, owner: UUID, episode_id: UUID) -> BookingContextRead:
        episode = await self._episode(owner, episode_id)
        eligibility = await self.repository.eligibility(owner, episode_id)
        providers = await self._providers(episode)
        return BookingContextRead(episode=CareEpisodeRead.model_validate(episode),
            eligibility=BookingEligibilityRead.model_validate(eligibility) if eligibility else None,
            providers=[CareProviderRead.model_validate(value) for value in providers],
            appointments=[self._read(value) for value in await self.repository.appointments(owner, episode_id)], server_time=self.now())

    async def precheck(self, owner: UUID, episode_id: UUID, body: BookingPrecheckWrite, request_id: str) -> BookingEligibilityRead:
        episode = await self._episode(owner, episode_id)
        self._bookable(episode)
        regions = {region for provider in await self._providers(episode) for region in provider.regions}
        reason = "emergency_help" if body.emergency_status != "clear" else "service_unsuitable" if not body.service_suitable else "region_unavailable" if body.region not in regions else ""
        check = BookingEligibility(owner_user_id=owner, episode_id=episode_id, region=body.region,
            service_suitable=body.service_suitable, emergency_status=body.emergency_status, eligible=not reason,
            reason=reason, expires_at=self.now() + timedelta(minutes=30), created_at=self.now())
        await self.repository.add(check)
        await self.audit.record(actor_user_id=owner, action="care.booking.eligibility_checked", resource_type="booking_eligibility",
            resource_id=str(check.id), request_id=request_id, details={"eligible": check.eligible})
        return BookingEligibilityRead.model_validate(check)

    async def availability(self, owner: UUID, episode_id: UUID, eligibility_id: UUID, provider_id: UUID, day: date) -> AvailabilityRead:
        episode = await self._episode(owner, episode_id)
        self._bookable(episode)
        eligibility = await self._eligibility(owner, episode_id, eligibility_id)
        provider = await self._provider(episode, provider_id, eligibility.region)
        today = self.now().astimezone(ZoneInfo(provider.timezone)).date()
        if day < today or day > today + timedelta(days=90):
            raise ApiError(code="invalid_date", message="Choose a date in the next 90 days.", status=422)
        rules = await self.repository.rules(provider_id)
        candidates = slots_for_day(day, provider.timezone, [(rule.weekday, rule.start_minute, rule.end_minute, rule.slot_minutes) for rule in rules])
        candidates = [(start, end) for start, end in candidates if start > self.now() and (episode.ends_at is None or start < episode.ends_at)]
        slots = []
        if candidates:
            first, last = candidates[0][0], max(end for _, end in candidates)
            blocks = await self.repository.blocks(provider_id, first, last)
            busy = await self.repository.busy(owner, provider_id, first, last, self.now())
            for start, end in candidates:
                free = not any(item.starts_at < end and item.ends_at > start for item in blocks) and not any(
                    item.starts_at < end and item.ends_at > start for item in busy)
                slots.append(AvailabilitySlot(starts_at=start, ends_at=end, available=free))
        return AvailabilityRead(provider_id=provider_id, date=day, timezone=provider.timezone, slots=slots, server_time=self.now())

    async def hold(self, owner: UUID, episode_id: UUID, body: HoldWrite, key: str, request_id: str) -> AppointmentRead:
        # The owner lock serializes own overlaps/idempotency; the provider lock serializes bookings and calendar edits.
        # Every appointment mutation takes locks in the same order: owner -> episode -> provider -> appointment.
        await self.repository.lock_owner(owner)
        episode = await self._episode(owner, episode_id, lock=True)
        decision = await self.idempotency.reserve(actor_user_id=owner, scope="care.appointment.hold", key=key,
            request_hash=request_hash({"episode_id": str(episode_id), **body.model_dump(mode="json")}), expires_at=self.now() + timedelta(days=1))
        if decision.status == "replay":
            return await self.read(owner, parse_idempotency_response_ref(decision.record.response_ref))
        self._bookable(episode)
        eligibility = await self._eligibility(owner, episode_id, body.eligibility_id)
        provider = await self._provider(episode, body.provider_id, eligibility.region, lock=True)
        await self.repository.expire_holds(owner, self.now())
        previous = [value for value in await self.repository.appointments(owner, episode_id) if value.status in {"held", "confirmed", "in_progress"}]
        if previous:
            raise ApiError(code="appointment_exists", message="An appointment already exists for this service. Refresh it before choosing another time.", status=409)
        day = body.starts_at.astimezone(ZoneInfo(provider.timezone)).date()
        availability = await self.availability(owner, episode_id, body.eligibility_id, provider.user_id, day)
        slot = next((item for item in availability.slots if item.starts_at == body.starts_at and item.available), None)
        if slot is None:
            raise ApiError(code="slot_unavailable", message="This time is no longer available. Choose another time.", status=409)
        appointment = CareAppointment(owner_user_id=owner, episode_id=episode_id, eligibility_id=eligibility.id,
            provider_id=provider.user_id, provider_name=provider.display_name, timezone=provider.timezone, region=eligibility.region,
            starts_at=slot.starts_at, ends_at=slot.ends_at, hold_expires_at=self.now() + timedelta(minutes=10), status="held", version=1)
        await self.repository.add(appointment)
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(appointment.id))
        await self._audit(owner, appointment, "held", request_id)
        return self._read(appointment)

    async def read(self, owner: UUID, appointment_id: UUID) -> AppointmentRead:
        return self._read(await self._appointment(owner, appointment_id))

    async def confirm(self, owner: UUID, appointment_id: UUID, body: VersionWrite, request_id: str) -> AppointmentRead:
        episode, appointment = await self.lock_for_owner(owner, appointment_id)
        if appointment.status == "confirmed" and appointment.version == body.expected_version + 1:
            return self._read(appointment)
        if appointment.status == "expired" or (appointment.status == "held" and appointment.hold_expires_at <= self.now()):
            raise ApiError(code="hold_expired", message="This hold expired. Choose a time again.", status=409)
        self._version(appointment, body.expected_version)
        if appointment.status != "held":
            raise ApiError(code="appointment_state", message="This appointment cannot be confirmed.", status=409)
        self._bookable(episode)
        eligibility = await self._eligibility(owner, episode.id, appointment.eligibility_id)
        await self._provider(episode, appointment.provider_id, eligibility.region)
        if appointment.starts_at <= self.now():
            raise ApiError(code="slot_unavailable", message="This time has already started.", status=409)
        appointment.status = "confirmed"
        appointment.confirmed_at = self.now()
        appointment.version += 1
        episode.assigned_ibclc_id = appointment.provider_id
        episode.stage = "initial_consultation" if episode.stage == "preparation" else episode.stage
        episode.version += 1
        await self.repository.flush()
        await self._audit(owner, appointment, "confirmed", request_id)
        await self._event(owner, appointment, "appointment_confirmed")
        return self._read(appointment)

    async def cancel(self, owner: UUID, appointment_id: UUID, body: VersionWrite, request_id: str) -> AppointmentRead:
        _, appointment = await self.lock_for_owner(owner, appointment_id)
        if appointment.status == "cancelled" and appointment.version == body.expected_version + 1:
            return self._read(appointment)
        self._version(appointment, body.expected_version)
        if appointment.status not in {"held", "confirmed", "expired"}:
            raise ApiError(code="appointment_state", message="This appointment cannot be cancelled now.", status=409)
        appointment.status = "cancelled"
        appointment.cancelled_at = self.now()
        appointment.version += 1
        from ..consultations.room_repository import RoomRepository
        await RoomRepository(self.repository.session).cancel(appointment.id, self.now())
        await self.repository.flush()
        await self._audit(owner, appointment, "cancelled", request_id)
        if appointment.confirmed_at is not None:
            await self._event(owner, appointment, "appointment_cancelled")
        return self._read(appointment)

    async def lock_for_owner(self, owner: UUID, appointment_id: UUID) -> tuple[CareEpisode, CareAppointment]:
        await self.repository.lock_owner(owner)
        initial = await self._appointment(owner, appointment_id)
        episode = await self._episode(owner, initial.episode_id, lock=True)
        await self.repository.provider(initial.provider_id, lock=True)
        return episode, await self._appointment(owner, appointment_id, lock=True)

    async def _episode(self, owner: UUID, episode_id: UUID, *, lock: bool = False) -> CareEpisode:
        episode = await self.repository.episode(owner, episode_id, lock=lock)
        if episode is None:
            raise ApiError(code="not_found", message="Service not found.", status=404)
        return episode

    def _bookable(self, episode: CareEpisode) -> None:
        if episode.status != "active" or episode.remaining_sessions < 1 or (episode.ends_at and episode.ends_at <= self.now()):
            raise ApiError(code="service_not_bookable", message="This service has no available consultation entitlement.", status=409)

    async def _providers(self, episode: CareEpisode) -> list[CareProvider]:
        sandbox = await self.repository.sandbox_episode(episode)
        return [] if sandbox and not self.sandbox_enabled else await self.repository.providers(sandbox=sandbox)

    async def _provider(self, episode: CareEpisode, provider_id: UUID, region: str, *, lock: bool = False) -> CareProvider:
        provider = await self.repository.provider(provider_id, lock=lock)
        sandbox = await self.repository.sandbox_episode(episode)
        if provider is None or not provider.active or provider.sandbox != sandbox or (sandbox and not self.sandbox_enabled) or region not in provider.regions:
            raise ApiError(code="region_unavailable", message="This specialist is unavailable for your current region.", status=409)
        return provider

    async def _eligibility(self, owner: UUID, episode_id: UUID, check_id: UUID) -> BookingEligibility:
        check = await self.repository.eligibility(owner, episode_id, check_id)
        if check is None:
            raise ApiError(code="not_found", message="Booking eligibility not found.", status=404)
        if not check.eligible or check.expires_at <= self.now():
            raise ApiError(code="eligibility_required", message="Confirm current service suitability and location again.", status=409)
        return check

    async def _appointment(self, owner: UUID, appointment_id: UUID, *, lock: bool = False) -> CareAppointment:
        appointment = await self.repository.appointment(owner, appointment_id, lock=lock)
        if appointment is None:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        return appointment

    def _read(self, appointment: CareAppointment) -> AppointmentRead:
        result = AppointmentRead.model_validate(appointment)
        if result.status == "held" and result.hold_expires_at <= self.now():
            result = result.model_copy(update={"status": "expired"})
        return result

    @staticmethod
    def _version(appointment: CareAppointment, expected: int) -> None:
        if appointment.version != expected:
            raise ApiError(code="version_conflict", message="This appointment changed. Refresh before continuing.", status=409)

    async def _audit(self, owner: UUID, appointment: CareAppointment, action: str, request_id: str) -> None:
        await self.audit.record(actor_user_id=owner, action=f"care.appointment.{action}", resource_type="care_appointment",
            resource_id=str(appointment.id), request_id=request_id, details={"version": appointment.version})

    async def _event(self, owner: UUID, appointment: CareAppointment, kind: Literal["appointment_confirmed", "appointment_cancelled"]) -> None:
        from ..care.events import record_care_event
        await record_care_event(self.repository.session, episode_id=appointment.episode_id, kind=kind,
            aggregate_id=appointment.id, aggregate_version=appointment.version, appointment_id=appointment.id,
            actor_user_id=owner, recipient_id=appointment.provider_id, occurred_at=self.now())
