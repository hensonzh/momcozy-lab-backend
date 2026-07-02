from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None


class AgentThreadRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    title: str
    status: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_json", serialization_alias="metadata")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgentThreadListResponse(BaseModel):
    items: list[AgentThreadRead]


class AgentRunCreate(BaseModel):
    thread_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    runtime_pattern: Literal["langgraph_sdk"] | None = None
    graph_version: str | None = Field(default=None, max_length=80)
    prompt_version: str | None = Field(default=None, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=255)


class AgentRunRead(BaseModel):
    id: UUID
    thread_id: UUID
    actor_user_id: UUID
    status: str
    runtime_pattern: str
    graph_version: str
    prompt_version: str
    request_id: str
    trace_id: str
    error_code: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentRunCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AgentEventRead(BaseModel):
    event_id: UUID
    thread_id: UUID
    run_id: UUID
    sequence: int
    type: str = Field(validation_alias="event_type", serialization_alias="type")
    payload: dict[str, Any]
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgentEventPage(BaseModel):
    items: list[AgentEventRead]
    next_sequence: int | None = None
