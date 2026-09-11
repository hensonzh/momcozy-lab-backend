from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..appointments.models import CareAppointment
from ..auth.current_user import CurrentUser
from ..care.event_models import CareServiceEvent
from ..care.models import CareEpisode
from ..users.models import User
from .models import Notification, NotificationPreference, PushInstallation
from .push_registration import PushRegistrationService, active_installations
from .schemas import NotificationOpenRead, NotificationRead, PushInstallationRead, PushInstallationWrite
from .templates import SERVICE_CATEGORIES, TEMPLATES

REMINDER_LEAD = timedelta(minutes=15)


class NotificationLifecycleService:
    def __init__(self, session: AsyncSession, *, push_available: bool = True, token_key: str = "", now: Callable[[], datetime] | None = None) -> None:
        self.session, self.push_available = session, push_available
        self.token_key = token_key
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def register(self, actor: CurrentUser, body: PushInstallationWrite) -> PushInstallationRead:
        result = await PushRegistrationService(self.session, token_key=self.token_key, now=self.now).register(actor, body)
        await self._sync_permission(actor.user_id)
        if result.permission in {"authorized", "provisional"} and result.token_registered and self.push_available:
            await self.restore_future_reminders(actor, result.installation_id)
        return result.model_copy(update={"push_available": self.push_available})

    async def detach(self, actor: CurrentUser, installation_id: UUID, secret: str) -> None:
        await PushRegistrationService(self.session, token_key=self.token_key, now=self.now).detach(actor, installation_id, secret)
        await self._sync_permission(actor.user_id)

    async def _sync_permission(self, owner: UUID) -> None:
        if not self.push_available or not await active_installations(self.session, owner, now=self.now()):
            await self.session.execute(update(Notification).where(Notification.owner_user_id == owner,
                Notification.send_status.in_(["scheduled", "pending"])).values(send_status="disabled"))

    async def open_notification(self, actor: CurrentUser, identifier: UUID, *, verify_conversation: Callable[[UUID, UUID], Awaitable[None]] | None = None) -> NotificationOpenRead:
        await self.lock_owner(actor.user_id)
        value = await self.session.scalar(select(Notification).where(Notification.id == identifier,
            Notification.owner_user_id == actor.user_id, Notification.status != "archived"))
        if value is None or (value.trigger_at is not None and value.trigger_at > self.now()):
            raise ApiError(code="not_found", message="Notification not found.", status=404)
        route = None
        if value.related_resource_type == "appointment" and value.related_resource_id:
            try:
                appointment = await self._appointment(actor.user_id, value.related_resource_id)
                suffix = "/room" if value.notification_type == "consultation_started" else "/summary" if value.notification_type in {"consultation_ended", "expert_feedback"} else ""
                route = f"/services/appointments/{appointment.id}{suffix}"
            except ApiError:
                pass
        elif value.related_resource_type == "service" and value.related_resource_id:
            episode = await self.session.scalar(select(CareEpisode).where(CareEpisode.id == value.related_resource_id, CareEpisode.owner_user_id == actor.user_id))
            if episode is not None:
                route = f"/services/episodes/{episode.id}"
        elif value.related_resource_type == "agent_conversation" and value.related_resource_id:
            if verify_conversation is None:
                raise ApiError(code="conversation_unavailable", message="The conversation service is unavailable.", status=503)
            try:
                await verify_conversation(actor.user_id, value.related_resource_id)
                route = f"/?conversationId={value.related_resource_id}"
            except ApiError as error:
                if error.status not in {403, 404}:
                    raise
        value.status, value.read_at = "read", value.read_at or self.now()
        await self.session.flush()
        return NotificationOpenRead(notification=NotificationRead.model_validate(value), route=route, resource_available=route is not None)

    async def lock_owner(self, owner: UUID) -> User:
        user = await self.session.scalar(select(User).where(User.id == owner).with_for_update().execution_options(populate_existing=True))
        if user is None or user.status != "active":
            raise ApiError(code="account_inactive", message="Account is not active.", status=403)
        return user

    async def preferences(self, owner: UUID) -> dict[str, bool]:
        values = {category: True for category in SERVICE_CATEGORIES}
        values["marketing"] = False
        for row in await self.session.scalars(select(NotificationPreference).where(NotificationPreference.owner_user_id == owner)):
            values[row.category] = row.enabled
        return values

    async def require_current_permission(self, actor: CurrentUser, installation_id: UUID) -> PushInstallation:
        if not self.push_available:
            raise ApiError(code="push_unavailable", message="Background notifications are unavailable. Your appointment is still saved.", status=503)
        for value in await active_installations(self.session, actor.user_id, now=self.now()):
            if value.id == installation_id and str(value.session_id) == actor.session_id and value.last_seen_at >= self.now() - timedelta(minutes=5):
                return value
        raise ApiError(code="notification_permission_required", message="Enable notifications in system settings to receive background reminders.", status=409)

    async def set_preference(self, actor: CurrentUser, category: str, enabled: bool, installation_id: UUID | None) -> dict[str, bool]:
        if category not in SERVICE_CATEGORIES:
            raise ApiError(code="validation_failed", message="This notification category is not available.", status=422)
        await self.lock_owner(actor.user_id)
        if enabled:
            if installation_id is None:
                raise ApiError(code="notification_permission_required", message="Check notification permission before enabling reminders.", status=409)
            await self.require_current_permission(actor, installation_id)
        await self.session.execute(insert(NotificationPreference).values(owner_user_id=actor.user_id, category=category,
            enabled=enabled, updated_at=self.now()).on_conflict_do_update(index_elements=["owner_user_id", "category"],
                set_={"enabled": enabled, "updated_at": self.now()}))
        if not enabled:
            await self.session.execute(update(Notification).where(Notification.owner_user_id == actor.user_id,
                Notification.category == category, Notification.send_status.in_(["pending", "scheduled"]))
                .values(send_status="disabled"))
        else:
            await self.restore_future_reminders(actor, installation_id)
        return await self.preferences(actor.user_id)

    async def set_appointment_reminder(self, actor: CurrentUser, appointment_id: UUID, *, enabled: bool,
                                       installation_id: UUID | None) -> Notification:
        await self.lock_owner(actor.user_id)
        appointment = await self._appointment(actor.user_id, appointment_id, lock=True)
        if enabled:
            if installation_id is None:
                raise ApiError(code="notification_permission_required", message="Check notification permission before enabling reminders.", status=409)
            await self.require_current_permission(actor, installation_id)
            if not (await self.preferences(actor.user_id))["appointments"]:
                raise ApiError(code="notification_preference_disabled", message="Enable appointment notifications in Notification settings.", status=409)
            if appointment.status != "confirmed" or appointment.starts_at - REMINDER_LEAD <= self.now():
                raise ApiError(code="reminder_expired", message="A reminder can only be enabled before its scheduled time.", status=409)
        return await self._reminder(appointment, requested=enabled)

    async def appointment_reminder(self, owner: UUID, appointment_id: UUID) -> Notification | None:
        appointment = await self._appointment(owner, appointment_id)
        key = f"appointment:{appointment.id}:reminder:{appointment.starts_at.isoformat()}"
        return cast(Notification | None, await self.session.scalar(select(Notification).where(
            Notification.owner_user_id == owner, Notification.idempotency_key == key)))

    async def restore_future_reminders(self, actor: CurrentUser, installation_id: UUID | None) -> int:
        if installation_id is None:
            return 0
        await self.lock_owner(actor.user_id)
        await self.require_current_permission(actor, installation_id)
        if not (await self.preferences(actor.user_id))["appointments"]:
            return 0
        values = list(await self.session.scalars(select(Notification).where(Notification.owner_user_id == actor.user_id,
            Notification.notification_type == "appointment_reminder", Notification.send_status == "disabled",
            Notification.status != "archived",
            Notification.trigger_at > self.now()).with_for_update()))
        restored = 0
        for value in values:
            if not value.payload.get("reminder_requested") or value.related_resource_id is None:
                continue
            try:
                appointment = await self._appointment(actor.user_id, value.related_resource_id)
            except ApiError:
                continue
            if appointment.status == "confirmed" and appointment.starts_at - REMINDER_LEAD > self.now():
                await self._reminder(appointment, requested=True)
                restored += 1
        return restored

    async def project_care_event(self, event: CareServiceEvent) -> None:
        episode = await self.session.get(CareEpisode, event.episode_id)
        if episode is None:
            return
        user = await self.session.scalar(select(User).where(User.id == episode.owner_user_id).with_for_update())
        if user is None or user.status != "active":
            return
        mapping = {"appointment_confirmed": "appointment_created", "consultation_started": "consultation_started",
            "consultation_completed": "consultation_ended", "consultation_technical_failure": "consultation_ended",
            "consultation_user_no_show": "consultation_ended", "plan_published": "expert_feedback",
            "service_progress_changed": "service_progress_updated", "appointment_cancelled": "service_progress_updated"}
        kind = mapping.get(event.kind)
        if kind is None:
            return
        appointment = None
        if event.appointment_id is not None:
            try:
                appointment = await self._appointment(episode.owner_user_id, event.appointment_id)
            except ApiError:
                return
        if event.kind == "appointment_confirmed" and appointment is not None:
            if appointment.status != "confirmed":
                return
            await self._reminder(appointment)
        if event.kind == "appointment_cancelled" and appointment is not None:
            await self.session.execute(update(Notification).where(Notification.owner_user_id == episode.owner_user_id,
                Notification.related_resource_id == appointment.id, Notification.notification_type == "appointment_reminder",
                Notification.send_status.in_(["pending", "scheduled", "disabled"]))
                .values(send_status="canceled", canceled_at=self.now()))
        if kind in {"appointment_created", "consultation_started", "consultation_ended", "expert_feedback"} and appointment is not None:
            suffix = "/room" if kind == "consultation_started" else "/summary" if kind in {"consultation_ended", "expert_feedback"} else ""
            route = f"/services/appointments/{appointment.id}{suffix}"
            resource_type, resource_id = "appointment", appointment.id
        else:
            route, resource_type, resource_id = f"/services/episodes/{episode.id}", "service", episode.id
        prefs = await self.preferences(episode.owner_user_id)
        template = TEMPLATES[kind]
        sendable = self.push_available and prefs[template.category] and bool(await active_installations(self.session, episode.owner_user_id, now=self.now()))
        await self._create(owner=episode.owner_user_id, kind=kind, key=f"care:{event.id}:{kind}", route=route,
            resource_type=resource_type, resource_id=resource_id, resource_version=event.aggregate_version,
            trigger_at=event.occurred_at, expires_at=event.occurred_at + timedelta(hours=1),
            send_status="pending" if sendable else "disabled")

    async def _appointment(self, owner: UUID, identifier: UUID, *, lock: bool = False) -> CareAppointment:
        query = select(CareAppointment).join(CareEpisode, CareEpisode.id == CareAppointment.episode_id).where(
            CareAppointment.id == identifier, CareAppointment.owner_user_id == owner, CareEpisode.owner_user_id == owner)
        if lock:
            query = query.with_for_update(of=CareAppointment)
        value = await self.session.scalar(query.execution_options(populate_existing=True))
        if value is None:
            raise ApiError(code="not_found", message="Appointment not found.", status=404)
        return value

    async def _reminder(self, appointment: CareAppointment, *, requested: bool | None = None) -> Notification:
        key = f"appointment:{appointment.id}:reminder:{appointment.starts_at.isoformat()}"
        previous = list(await self.session.scalars(select(Notification).where(Notification.owner_user_id == appointment.owner_user_id,
            Notification.related_resource_id == appointment.id, Notification.notification_type == "appointment_reminder")
            .order_by(Notification.created_at.desc(), Notification.id.desc()).with_for_update()))
        current = next((value for value in previous if value.idempotency_key == key), None)
        latest = current or (previous[0] if previous else None)
        opt_in = requested if requested is not None else bool(latest and latest.payload.get("reminder_requested"))
        for old in previous:
            if old is not current and old.send_status not in {"sent", "canceled", "expired"}:
                old.send_status, old.canceled_at = "canceled", self.now()
        due = appointment.starts_at - REMINDER_LEAD
        # Event replay must preserve a task already enabled before its due time.
        # Recovery of a disabled task still requires a strictly future trigger.
        already_enabled = current is not None and current.send_status in {"scheduled", "pending"} and appointment.starts_at > self.now()
        sendable = (opt_in and appointment.status == "confirmed" and (due > self.now() or already_enabled) and self.push_available
            and (await self.preferences(appointment.owner_user_id))["appointments"]
            and bool(await active_installations(self.session, appointment.owner_user_id, now=self.now())))
        if current is None:
            current = await self._create(owner=appointment.owner_user_id, kind="appointment_reminder", key=key,
                route=f"/services/appointments/{appointment.id}", resource_type="appointment", resource_id=appointment.id,
                resource_version=appointment.version, trigger_at=due, expires_at=appointment.starts_at,
                send_status="scheduled" if sendable else "disabled", payload={"reminder_requested": bool(opt_in)})
        elif current.send_status not in {"sent", "expired", "canceled"}:
            current.payload = {**current.payload, "reminder_requested": bool(opt_in)}
            current.send_status = ("pending" if current.send_status == "pending" else "scheduled") if sendable else "disabled"
        await self.session.flush()
        return current

    async def _create(self, *, owner: UUID, kind: str, key: str, route: str, resource_type: str, resource_id: UUID,
                      resource_version: int, trigger_at: datetime, expires_at: datetime, send_status: str,
                      payload: dict[str, Any] | None = None) -> Notification:
        template = TEMPLATES[kind]
        await self.session.execute(insert(Notification).values(id=uuid4(), owner_user_id=owner, notification_type=kind,
            title=template.title, body=template.body, status="unread", source="care", category=template.category,
            trigger_type="scheduled" if kind == "appointment_reminder" else "immediate", trigger_at=trigger_at,
            expires_at=expires_at, send_status=send_status, related_resource_type=resource_type, related_resource_id=resource_id,
            resource_version=resource_version, route=route, idempotency_key=key, payload=payload or {}, created_at=self.now())
            .on_conflict_do_nothing(constraint="uq_notifications_idempotency_key"))
        value = await self.session.scalar(select(Notification).where(Notification.idempotency_key == key))
        assert value is not None
        return value
