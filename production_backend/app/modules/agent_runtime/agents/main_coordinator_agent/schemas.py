from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ServiceSkillId(StrEnum):
    COZYMATE_SERVICE_AGENT = "cozymate_service_agent"
    BIRTH_PREP = "birth-prep"
    MILK_MANAGEMENT = "milk-management"
    HEALTH_CONSULTATION = "health-consultation"
    EMOTION_SUPPORT = "emotion-support"
    DEVICE_GUIDANCE = "device-guidance"


class RoutingSource(StrEnum):
    PASSTHROUGH = "passthrough"


class IntentItem(BaseModel):
    intent_type: str
    service_skill_id: ServiceSkillId
    priority: int = 50
    requires_write: bool = False
    safety_sensitive: bool = False
    depends_on: list[str] = Field(default_factory=list)


class RoutingPlan(BaseModel):
    selected_skill_id: ServiceSkillId
    intents: list[IntentItem]
    execution_mode: Literal["passthrough"] = "passthrough"
    confidence: float = Field(ge=0, le=1)
    source: RoutingSource
    reason_codes: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
