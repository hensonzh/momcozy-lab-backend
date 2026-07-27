from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SupportIssueType = Literal[
    "malfunction",
    "missing_parts",
    "defect",
    "warranty",
    "return_or_refund",
    "order_or_shipping",
    "usage_help",
    "safety_concern",
    "other",
]
SupportUrgency = Literal["normal", "high", "safety"]


class AgentSupportTicketPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    operation: Literal["create"]
    issue_type: SupportIssueType = "other"
    issue_summary: str = Field(min_length=1, max_length=2000)
    product_model: str = Field(default="", max_length=120)
    order_number: str = Field(default="", max_length=120)
    purchase_channel: str = Field(default="", max_length=120)
    user_contact: str = Field(default="", max_length=255)
    troubleshooting_done: list[
        Annotated[str, Field(min_length=1, max_length=500)]
    ] = Field(
        default_factory=list,
        max_length=20,
    )
    urgency: SupportUrgency = "normal"
    user_emotion: str = Field(default="", max_length=500)
    attachments_note: str = Field(default="", max_length=1000)
    locale: str = Field(default="", max_length=35)


class AgentSupportTicketApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: AgentSupportTicketPayload


class AgentSupportTicketApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["support_ticket"]
    resource_id: str
    details: dict[str, str] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)
