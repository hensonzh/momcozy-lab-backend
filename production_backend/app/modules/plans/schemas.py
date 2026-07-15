from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    plan_type: str
    title: str
    summary: str
    status: str
    source: str
    payload: dict[str, Any]
    version: int

    @field_validator("version", mode="before")
    @classmethod
    def default_legacy_version(cls, value: object) -> int:
        return value if isinstance(value, int) and value >= 1 else 1

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


class PlanTaskUpdate(BaseModel):
    plan_id: UUID | None = None
    task_date: date | None = None
    task_time: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    payload: dict[str, Any] | None = None


class PlanTaskCompletionUpdate(BaseModel):
    completed: bool = True


class PlanTaskStateUpdate(BaseModel):
    state: Literal["pending", "completed", "skipped"]


class PlanTodoCompletionUpdate(BaseModel):
    completed: bool
    expected_version: int = Field(ge=1)


class PlanTaskListResponse(BaseModel):
    items: list[PlanTaskRead]
