from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class StatusProfileSummary(BaseModel):
    display_name: str = ""
    daily_summary: str = ""
    lactation_advice: str = ""
    feeding_advice: str = ""


class StatusPageTodayRead(BaseModel):
    date: date
    profile: StatusProfileSummary
    infant_count: int
    feeding_count: int
    pumping_count: int
    pumped_milk_volume_ml: float
    task_count: int
    completed_task_count: int
    pending_task_count: int
    unread_notification_count: int
