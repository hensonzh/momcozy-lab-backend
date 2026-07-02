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


class AgentActionRead(BaseModel):
    id: UUID
    run_id: UUID
    actor_user_id: UUID
    action_type: str
    target_type: str
    target_id: str
    status: str
    side_effect_level: str
    preview_payload: dict[str, Any]
    idempotency_key: str
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    applied_at: datetime | None = None
    failed_at: datetime | None = None
    error_code: str

    model_config = ConfigDict(from_attributes=True)


class AgentActionConfirm(BaseModel):
    edited_apply_payload: dict[str, Any] | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)


class AgentActionReject(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AgentReplayBundle(BaseModel):
    run: dict[str, Any]
    messages: list[dict[str, Any]]
    events: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    checkpoints: list[dict[str, Any]]
    workflow_states: list[dict[str, Any]]
    context_projections: list[dict[str, Any]]
    safety_events: list[dict[str, Any]]


class AgentEvalCaseCreate(BaseModel):
    suite: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(default="", max_length=120)
    owner_team: str = Field(default="", max_length=120)


class AgentEvalCaseRead(BaseModel):
    id: UUID
    suite: str
    name: str
    domain: str
    input_payload: dict[str, Any]
    expected_behavior: dict[str, Any]
    expected_tool_calls: list[Any]
    expected_safety_decision: str
    source_run_id: UUID | None = None
    status: str
    owner_team: str

    model_config = ConfigDict(from_attributes=True)
