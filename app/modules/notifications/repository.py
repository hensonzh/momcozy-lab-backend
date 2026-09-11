from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification


class NotificationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page_for_owner(self, *, owner_user_id: UUID, limit: int, status: str | None,
                             notification_type: str | None, before: tuple[datetime, UUID] | None) -> list[Notification]:
        conditions = [Notification.owner_user_id == owner_user_id,
            or_(Notification.trigger_at.is_(None), Notification.trigger_at <= datetime.now(timezone.utc)),
            Notification.send_status != "canceled"]
        conditions.append(Notification.status == status if status else Notification.status != "archived")
        if notification_type:
            conditions.append(Notification.notification_type == notification_type)
        if before:
            at, identifier = before
            conditions.append(or_(Notification.created_at < at, and_(Notification.created_at == at, Notification.id < identifier)))
        return list(await self.session.scalars(select(Notification).where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)))

    async def unread_count(self, *, owner_user_id: UUID) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(Notification).where(
            Notification.owner_user_id == owner_user_id, Notification.status == "unread", Notification.send_status != "canceled",
            or_(Notification.trigger_at.is_(None), Notification.trigger_at <= datetime.now(timezone.utc)))) or 0)

    async def mark_all_read(self, *, owner_user_id: UUID, now: datetime) -> int:
        result = await self.session.execute(update(Notification).where(Notification.owner_user_id == owner_user_id,
            Notification.status == "unread", Notification.send_status != "canceled",
            or_(Notification.trigger_at.is_(None), Notification.trigger_at <= now)).values(status="read", read_at=now))
        return cast(CursorResult[Any], result).rowcount

    async def create_notification(
        self,
        *,
        owner_user_id: UUID,
        notification_type: str,
        title: str,
        body: str,
        source: str,
        payload: dict[str, Any],
        delivered_at: datetime | None,
    ) -> Notification:
        notification = Notification(
            owner_user_id=owner_user_id,
            notification_type=notification_type,
            title=title,
            body=body,
            source=source,
            payload=payload,
            delivered_at=delivered_at,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def get_for_owner(self, *, notification_id: UUID, owner_user_id: UUID) -> Notification | None:
        statement = select(Notification).where(
            Notification.id == notification_id,
            Notification.owner_user_id == owner_user_id,
            Notification.status != "archived",
        )
        return cast(Notification | None, await self.session.scalar(statement))

    async def list_for_owner(
        self,
        *,
        owner_user_id: UUID,
        status: str | None,
        notification_type: str | None,
        limit: int,
    ) -> list[Notification]:
        conditions = [Notification.owner_user_id == owner_user_id]
        if status:
            conditions.append(Notification.status == status)
        else:
            conditions.append(Notification.status != "archived")
        if notification_type:
            conditions.append(Notification.notification_type == notification_type)
        statement = select(Notification).where(*conditions).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def set_read_state(
        self,
        *,
        notification_id: UUID,
        owner_user_id: UUID,
        read: bool,
        read_at: datetime | None,
    ) -> Notification | None:
        # Match the worker's User -> Notification order before mutation/audit FKs.
        from ..users.models import User
        await self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "read" if read else "unread"
        notification.read_at = read_at if read else None
        await self.session.flush()
        return notification

    async def archive(self, *, notification_id: UUID, owner_user_id: UUID) -> Notification | None:
        # Match the worker's User -> Notification order before mutation/audit FKs.
        from ..users.models import User
        await self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "archived"
        await self.session.flush()
        return notification

    async def get_milk_reminder_for_owner(
        self,
        *,
        reminder_id: UUID,
        owner_user_id: UUID,
    ) -> Notification | None:
        statement = select(Notification).where(
            Notification.id == reminder_id,
            Notification.owner_user_id == owner_user_id,
            Notification.notification_type == "milk_reminder",
            Notification.status != "archived",
        )
        return cast(Notification | None, await self.session.scalar(statement))

    async def delete_milk_reminder(
        self,
        *,
        reminder: Notification,
    ) -> None:
        await self.session.delete(reminder)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
