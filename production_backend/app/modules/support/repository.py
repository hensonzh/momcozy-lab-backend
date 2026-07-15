from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SupportTicket


class SupportTicketsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_ticket(
        self,
        *,
        owner_user_id: UUID,
        ticket_number: str,
        issue_type: str,
        issue_summary: str,
        product_model: str,
        order_number: str,
        purchase_channel: str,
        user_contact: str,
        urgency: str,
        source: str,
        payload: dict[str, Any],
        submitted_at: datetime,
    ) -> SupportTicket:
        ticket = SupportTicket(
            owner_user_id=owner_user_id,
            ticket_number=ticket_number,
            issue_type=issue_type,
            issue_summary=issue_summary,
            product_model=product_model,
            order_number=order_number,
            purchase_channel=purchase_channel,
            user_contact=user_contact,
            urgency=urgency,
            source=source,
            payload=payload,
            submitted_at=submitted_at,
        )
        self.session.add(ticket)
        await self.session.flush()
        return ticket

    async def get_for_owner(self, *, ticket_id: UUID, owner_user_id: UUID) -> SupportTicket | None:
        statement = select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.owner_user_id == owner_user_id)
        return cast(SupportTicket | None, await self.session.scalar(statement))

    async def list_for_owner(
        self,
        *,
        owner_user_id: UUID,
        status: str | None,
        limit: int,
    ) -> list[SupportTicket]:
        conditions = [SupportTicket.owner_user_id == owner_user_id]
        if status:
            conditions.append(SupportTicket.status == status)
        statement = select(SupportTicket).where(*conditions).order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())
