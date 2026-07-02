from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    plan_type: str
    title: str
    summary: str
    status: str
    source: str
    payload: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class PlanCreate(BaseModel):
    plan_type: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    source: str | None = Field(default="manual", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanListResponse(BaseModel):
    items: list[PlanRead]


class PlanTaskRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str
    title: str
    description: str
    status: str
    payload: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class PlanTaskCreate(BaseModel):
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str | None = Field(default=None, max_length=16)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanTaskCompletionUpdate(BaseModel):
    completed: bool = True


class PlanTaskListResponse(BaseModel):
    items: list[PlanTaskRead]
