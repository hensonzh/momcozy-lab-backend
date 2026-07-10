from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AgentId(StrEnum):
    COZYMATE_SERVICE_AGENT = "cozymate_service_agent"


class RoutingSource(StrEnum):
    PASSTHROUGH = "passthrough"


class IntentItem(BaseModel):
    intent_type: str
    agent_id: AgentId | None = None
    priority: int = 50
    requires_write: bool = False
    safety_sensitive: bool = False
    depends_on: list[str] = Field(default_factory=list)


class RoutingPlan(BaseModel):
    target_kind: Literal["agent"] = "agent"
    selected_agent_id: AgentId = AgentId.COZYMATE_SERVICE_AGENT
    intents: list[IntentItem]
    execution_mode: Literal["passthrough"] = "passthrough"
    confidence: float = Field(ge=0, le=1)
    source: RoutingSource
    reason_codes: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
