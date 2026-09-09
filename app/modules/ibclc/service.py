from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from ...core.errors import ApiError
from ...core.settings import Settings
from ..appointments.schemas import AppointmentRead
from ..audit.service import AuditService
from ..auth import CurrentUser
from ..auth.models import DeviceSession
from ..care.catalog import PACKAGES
from ..care.models import CareProvider
from ..care.schemas import CareEpisodeRead, CareProviderRead
from ..consultations.room_schemas import ConsultationRead
from .repository import AppointmentProjection, CaseProjection, WorkbenchRepository
from .reminders_repository import WorkbenchRemindersRepository
from .followups_repository import FollowupsRepository
from .schemas import FollowupClientRead, FollowupServiceRead, WorkbenchFollowupsRead
from .schemas import WorkbenchReminderRead, WorkbenchRemindersRead
from .schemas import (ClientServiceRead, WorkbenchAppointmentRead, WorkbenchAppointmentsRead, WorkbenchClientDetailRead,
    WorkbenchClientRead, WorkbenchClientsRead, WorkbenchIdentityRead, WorkbenchCalendarRead)


class WorkbenchService:
    def __init__(self, repository: WorkbenchRepository, audit: AuditService, settings: Settings,
        *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.repository, self.audit, self.settings, self.now = repository, audit, settings, now

    async def _provider(self, actor: CurrentUser) -> CareProvider:
        if "ibclc" not in actor.roles:
            raise ApiError(code="permission_denied", message="Workbench access requires an IBCLC account.", status=403)
        provider = await self.repository.provider(actor.user_id)
        if provider is None or not provider.active:
            raise ApiError(code="permission_denied", message="Workbench access is not active.", status=403)
        return provider

    async def reminders(self, actor: CurrentUser, *, offset: int, limit: int, request_id: str) -> WorkbenchRemindersRead:
        await self._provider(actor)
        rows, total, unread = await WorkbenchRemindersRepository(self.repository.session).list(actor.user_id, offset=offset, limit=limit)
        await self._audit(actor, 'reminders_viewed', request_id, len(rows))
        items = [WorkbenchReminderRead.model_validate({
            'id': row.event.id, 'kind': row.event.kind, 'episode_id': row.event.episode_id,
            'appointment_id': row.event.appointment_id, 'patient_ref': row.patient_ref,
            'patient_name': row.patient_name, 'package_name': PACKAGES[row.package_id].name,
            'starts_at': row.starts_at, 'timezone': row.timezone,
            'occurred_at': row.event.occurred_at, 'read_at': row.read_at,
            'report_date': row.report_date,
        }) for row in rows]
        return WorkbenchRemindersRead(items=items, total=total, unread_count=unread, offset=offset, limit=limit, server_time=self.now())

    async def followups(self, actor: CurrentUser, *, query: str, status: str, offset: int, limit: int, request_id: str) -> WorkbenchFollowupsRead:
        provider = await self._provider(actor)
        zone = ZoneInfo(provider.timezone)
        day = self.now().astimezone(zone).date()
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        rows, total, all_count, completed = await FollowupsRepository(self.repository).list(actor.user_id, day, start, end,
            query=query.strip(), status=status, offset=offset, limit=limit)
        grouped: dict[UUID, FollowupClientRead] = {}
        for value in rows:
            owner = value.case.episode.owner_user_id
            item = grouped.setdefault(owner, FollowupClientRead(patient_ref=owner, name=value.case.name, services=[], status='completed'))
            if item.name is None:
                item.name = value.case.name
            item.services.append(FollowupServiceRead.model_validate({'service': ClientServiceRead(episode=self._episode(value.case),
                package=PACKAGES[value.case.episode.package_id], case_consent=value.case.granted),
                'ai_consent': value.case.granted and value.metadata['ai'], 'purpose': value.metadata['purpose'], 'report_id': value.metadata['report_id'],
                'report_status': value.metadata['report_status'], 'review_decision': value.metadata['review']}))
            if not value.metadata['done']:
                item.status = 'pending'
        items = sorted(grouped.values(), key=lambda item: (item.status == 'completed', item.patient_ref))
        await self._audit(actor, 'followups_viewed', request_id, len(items))
        return WorkbenchFollowupsRead(date=day, timezone=provider.timezone, items=items, total=total, all_count=all_count,
            pending_count=all_count-completed, completed_count=completed, offset=offset, limit=limit, server_time=self.now())

    async def read_reminder(self, actor: CurrentUser, event_id: UUID, request_id: str) -> None:
        await self._provider(actor)
        found = await WorkbenchRemindersRepository(self.repository.session).mark_read(actor.user_id, event_id, self.now())
        if not found:
            raise ApiError(code='not_found', message='Work reminder not found.', status=404)
        await self._audit(actor, 'reminder_read', request_id, 1)

    async def identity(self, actor: CurrentUser) -> WorkbenchIdentityRead:
        provider = await self._provider(actor)
        device = await self.repository.session.get(DeviceSession, UUID(actor.session_id))
        if device is None or device.mfa_verified_at is None:
            raise ApiError(code="mfa_required", message="Sign in to the workbench again.", status=401)
        return WorkbenchIdentityRead(provider=CareProviderRead.model_validate(provider), email=await self.repository.email(actor.user_id),
            mfa_expires_at=device.mfa_verified_at + timedelta(hours=self.settings.ibclc_session_hours), server_time=self.now())

    async def appointments(self, actor: CurrentUser, *, day: date | None, offset: int, limit: int, request_id: str) -> WorkbenchAppointmentsRead:
        provider = await self._provider(actor)
        zone = ZoneInfo(provider.timezone)
        selected = day or self.now().astimezone(zone).date()
        start = datetime.combine(selected, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(selected + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        rows, total = await self.repository.appointments(actor.user_id, start=start, end=end, offset=offset, limit=limit)
        await self._audit(actor, "appointments_viewed", request_id, len(rows))
        return WorkbenchAppointmentsRead(date=selected, timezone=provider.timezone, items=[self._appointment(value) for value in rows],
            total=total, offset=offset, limit=limit, server_time=self.now())

    async def clients(self, actor: CurrentUser, *, query: str, status: str, offset: int, limit: int, request_id: str) -> WorkbenchClientsRead:
        await self._provider(actor)
        owners, total = await self.repository.clients(actor.user_id, query=query.strip(), status=status, offset=offset, limit=limit)
        cases = await self.repository.cases(actor.user_id, owners)
        items = self._clients(cases)
        await self._audit(actor, "clients_viewed", request_id, len(items))
        return WorkbenchClientsRead(items=items, total=total, offset=offset, limit=limit, server_time=self.now())

    async def calendar(self, actor: CurrentUser, *, selected: date | None, offset: int, limit: int, request_id: str) -> WorkbenchCalendarRead:
        provider = await self._provider(actor)
        zone = ZoneInfo(provider.timezone)
        day = selected or self.now().astimezone(zone).date()
        week_start = day - timedelta(days=day.weekday())
        start = datetime.combine(week_start, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(week_start + timedelta(days=7), time.min, zone).astimezone(timezone.utc)
        rows, total = await self.repository.appointments(actor.user_id, start=start, end=end, offset=offset, limit=limit, include_cancelled=False, overlap=True)
        await self._audit(actor, "calendar_viewed", request_id, len(rows))
        return WorkbenchCalendarRead(week_start=week_start, timezone=provider.timezone, items=[self._appointment(value) for value in rows],
            total=total, offset=offset, limit=limit, server_time=self.now())

    async def client_detail(self, actor: CurrentUser, owner: UUID, request_id: str, *, offset: int = 0, limit: int = 20) -> WorkbenchClientDetailRead:
        await self._provider(actor)
        cases = await self.repository.cases(actor.user_id, [owner])
        if not cases:
            raise ApiError(code="not_found", message="Client not found.", status=404)
        rows, total = await self.repository.appointments(actor.user_id, owner=owner, offset=offset, limit=limit)
        await self._audit(actor, "client_viewed", request_id, 1)
        return WorkbenchClientDetailRead(client=self._clients(cases)[0], appointments=[self._appointment(value) for value in rows],
            appointment_total=total, offset=offset, limit=limit, server_time=self.now())

    @staticmethod
    def _episode(value: CaseProjection) -> CareEpisodeRead:
        episode = CareEpisodeRead.model_validate(value.episode)
        return episode if value.granted else episode.model_copy(update={"baby_id": None})

    @classmethod
    def _appointment(cls, value: AppointmentProjection) -> WorkbenchAppointmentRead:
        return WorkbenchAppointmentRead(appointment=AppointmentRead.model_validate(value.appointment), episode=cls._episode(value.case),
            package=PACKAGES[value.case.episode.package_id], patient_ref=value.appointment.owner_user_id,
            patient_name=value.case.name, delivery_date=value.case.delivery_date, case_consent=value.case.granted,
            consultation=ConsultationRead.model_validate(value.consultation) if value.consultation else None,
            note_status=value.note_status, published_revision=value.published_revision)

    @classmethod
    def _clients(cls, values: list[CaseProjection]) -> list[WorkbenchClientRead]:
        grouped: dict[UUID, list[CaseProjection]] = {}
        for value in values:
            grouped.setdefault(value.episode.owner_user_id, []).append(value)
        return [WorkbenchClientRead(patient_ref=owner, name=next((value.name for value in cases if value.name), None),
            delivery_date=next((value.delivery_date for value in cases if value.delivery_date), None),
            services=[ClientServiceRead(episode=cls._episode(value), package=PACKAGES[value.episode.package_id], case_consent=value.granted) for value in cases])
            for owner, cases in sorted(grouped.items())]

    async def _audit(self, actor: CurrentUser, action: str, request_id: str, count: int) -> None:
        await self.audit.record(actor_user_id=actor.user_id, action=f"workbench.{action}", resource_type="care_provider", resource_id=str(actor.user_id),
            request_id=request_id, details={"result_count": count})
