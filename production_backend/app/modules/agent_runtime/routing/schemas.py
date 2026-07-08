from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ServiceSkillId(StrEnum):
    GENERAL = "general_assistant"
    PREGNANCY = "pregnancy_service"
    LACTATION = "lactation"
    POSTPARTUM = "postpartum_recovery"
    AFTER_SALES = "after_sales"
    SAFETY = "safety_guardrail"


class RoutingSource(StrEnum):
    PENDING_ACTION = "pending_action"
    SAFETY_RULE = "safety_rule"
    APP_SURFACE = "app_surface"
    ACTIVE_WORKFLOW = "active_workflow"
    LOCAL_HINT = "local_hint"
    COMPLEXITY_RULE = "complexity_rule"
    MODEL_PLANNER = "model_planner"
    FALLBACK = "fallback"


class RoutingContext(BaseModel):
    run_id: UUID
    thread_id: UUID
    actor_user_id: UUID
    message: str = ""
    app_surface: str | None = None
    active_service_skill_id: ServiceSkillId | None = None
    active_workflow: str | None = None
    pending_action_id: UUID | None = None
    attachment_types: list[str] = Field(default_factory=list)


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
    tool_group_ids: list[str] = Field(default_factory=list)
    execution_mode: Literal["single", "single_with_note", "blocked_for_safety"] = "single"
    confidence: float = Field(ge=0, le=1)
    source: RoutingSource
    reason_codes: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
