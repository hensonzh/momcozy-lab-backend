from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


MilkReminderOperation = Literal["create", "update", "delete", "disable"]


class AgentMilkReminderPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    operation: MilkReminderOperation
    reminder_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=2000)
    remind_at: datetime | None = None
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_operation_fields(self) -> AgentMilkReminderPayload:
        supplied = self.model_fields_set
        mutable_fields = {"title", "body", "remind_at", "payload"}
        if self.operation == "create":
            if self.reminder_id is not None:
                raise ValueError("reminder_id is not accepted for create")
            if self.title is None or self.remind_at is None:
                raise ValueError("title and remind_at are required for create")
        elif self.operation == "update":
            if self.reminder_id is None:
                raise ValueError("reminder_id is required for update")
            if not supplied.intersection(mutable_fields):
                raise ValueError("at least one reminder field is required for update")
            if "title" in supplied and self.title is None:
                raise ValueError("title cannot be null")
            if "remind_at" in supplied and self.remind_at is None:
                raise ValueError("remind_at cannot be null")
        else:
            if self.reminder_id is None:
                raise ValueError(
                    f"reminder_id is required for {self.operation}"
                )
            if supplied.intersection(mutable_fields):
                raise ValueError(
                    f"reminder fields are not accepted for {self.operation}"
                )
        if self.remind_at is not None and self.remind_at.tzinfo is None:
            raise ValueError("remind_at must include a timezone offset")
        return self


class AgentMilkReminderApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: AgentMilkReminderPayload


class AgentMilkReminderApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["milk_reminder"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)
