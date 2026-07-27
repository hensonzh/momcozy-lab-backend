from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


DiaryOperation = Literal["create", "update", "delete"]


class AgentDiaryApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: DiaryOperation
    entry_date: date
    content: str | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> AgentDiaryApplyPayload:
        if self.operation in {"create", "update"} and not self.content:
            raise ValueError("content is required for create and update")
        if self.operation == "delete" and "content" in self.model_fields_set:
            raise ValueError("content is not accepted for delete")
        return self


class AgentDiaryApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: AgentDiaryApplyPayload


class AgentDiaryReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["entry_read", "entry_not_found", "entries_read"]
    side_effect_performed: Literal[False] = False
    entry_date: date | None = None
    entry: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class AgentDiaryApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["pregnancy_diary_entry"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)
