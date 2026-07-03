from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from ..notifications import NotificationsService
from ..plans import PlansService
from ..profiles import ProfileService
from ..records import RecordsService
from .schemas import StatusPageTodayRead, StatusProfileSummary


class StatusPageService:
    def __init__(
        self,
        *,
        profile_service: ProfileService,
        records_service: RecordsService,
        plans_service: PlansService,
        notifications_service: NotificationsService,
    ) -> None:
        self.profile_service = profile_service
        self.records_service = records_service
        self.plans_service = plans_service
        self.notifications_service = notifications_service

    async def get_today_status(self, *, owner_user_id: UUID, day: date) -> StatusPageTodayRead:
        start_at = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end_at = start_at + timedelta(days=1)
        profile = await self.profile_service.get_user_profile(user_id=owner_user_id)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        feedings = await self.records_service.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        pumpings = await self.records_service.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, task_date=day, limit=100)
        unread_notifications = await self.notifications_service.list_notifications(
            owner_user_id=owner_user_id,
            status="unread",
            limit=50,
        )
        completed_task_count = sum(1 for task in tasks if _is_completed_task(task))
        task_count = len(tasks)
        return StatusPageTodayRead(
            date=day,
            profile=StatusProfileSummary(
                display_name=str(getattr(profile, "display_name", "") or ""),
                daily_summary=str(getattr(profile, "daily_summary", "") or ""),
                lactation_advice=str(getattr(profile, "lactation_advice", "") or ""),
                feeding_advice=str(getattr(profile, "feeding_advice", "") or ""),
            ),
            infant_count=len(infants),
            feeding_count=len(feedings),
            pumping_count=len(pumpings),
            pumped_milk_volume_ml=_pumped_milk_volume(pumpings),
            task_count=task_count,
            completed_task_count=completed_task_count,
            pending_task_count=max(task_count - completed_task_count, 0),
            unread_notification_count=len(unread_notifications),
        )


def _is_completed_task(task: Any) -> bool:
    return str(getattr(task, "status", "") or "") == "completed" or getattr(task, "completed_at", None) is not None


def _pumped_milk_volume(pumpings: list[Any]) -> float:
    total = 0.0
    for pumping in pumpings:
        try:
            total += float(getattr(pumping, "milk_volume_ml", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)
