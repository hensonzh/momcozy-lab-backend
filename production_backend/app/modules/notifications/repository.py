from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification


class NotificationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "read" if read else "unread"
        notification.read_at = read_at if read else None
        await self.session.flush()
        return notification

    async def archive(self, *, notification_id: UUID, owner_user_id: UUID) -> Notification | None:
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "archived"
        await self.session.flush()
        return notification
