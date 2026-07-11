from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ....core.errors import ApiError


@dataclass(frozen=True)
class AgentActionPolicyDecision:
    action_type: str
    target_type: str
    side_effect_level: str
    requires_confirmation: bool


@dataclass(frozen=True)
class AgentActionPolicyRule:
    action_type: str
    target_type: str
    side_effect_level: str
    requires_confirmation: bool = True


DEFAULT_AGENT_ACTION_RULES: Mapping[str, AgentActionPolicyRule] = {
    "hospital_bag.cart.update": AgentActionPolicyRule(
        action_type="hospital_bag.cart.update",
        target_type="hospital_bag_cart",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "support.ticket.create": AgentActionPolicyRule(
        action_type="support.ticket.create",
        target_type="support_ticket",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "records.feeding_record.create": AgentActionPolicyRule(
        action_type="records.feeding_record.create",
        target_type="feeding_record",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "records.pumping_record.create": AgentActionPolicyRule(
        action_type="records.pumping_record.create",
        target_type="pumping_record",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "records.feeding_record.delete": AgentActionPolicyRule(
        action_type="records.feeding_record.delete",
        target_type="feeding_record",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "records.pumping_record.delete": AgentActionPolicyRule(
        action_type="records.pumping_record.delete",
        target_type="pumping_record",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "records.growth_record.create": AgentActionPolicyRule(
        action_type="records.growth_record.create",
        target_type="growth_record",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "records.growth_record.update": AgentActionPolicyRule(
        action_type="records.growth_record.update",
        target_type="growth_record",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "records.growth_record.delete": AgentActionPolicyRule(
        action_type="records.growth_record.delete",
        target_type="growth_record",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.milk_plan.create": AgentActionPolicyRule(
        action_type="plans.milk_plan.create",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "pregnancy.plan.create": AgentActionPolicyRule(
        action_type="pregnancy.plan.create",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.task.create": AgentActionPolicyRule(
        action_type="plans.task.create",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.task.complete": AgentActionPolicyRule(
        action_type="plans.task.complete",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.task.update": AgentActionPolicyRule(
        action_type="plans.task.update",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.task.delete": AgentActionPolicyRule(
        action_type="plans.task.delete",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "plans.plan.delete": AgentActionPolicyRule(
        action_type="plans.plan.delete",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "notifications.milk_reminder.create": AgentActionPolicyRule(
        action_type="notifications.milk_reminder.create",
        target_type="notification",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
    "pregnancy_diary.entry.delete": AgentActionPolicyRule(
        action_type="pregnancy_diary.entry.delete",
        target_type="pregnancy_diary_entry",
        side_effect_level="medium",
        requires_confirmation=True,
    ),
}


class AgentActionPolicy:
    def __init__(self, *, rules: Mapping[str, AgentActionPolicyRule] | None = None) -> None:
        self.rules = rules or DEFAULT_AGENT_ACTION_RULES

    def validate(
        self,
        *,
        action_type: str,
        target_type: str = "",
        side_effect_level: str = "",
    ) -> AgentActionPolicyDecision:
        rule = self.rules.get(action_type)
        if rule is None:
            raise ApiError(code="unsupported_agent_action", message="Agent action type is not supported.", status=422)

        normalized_target_type = target_type or rule.target_type
        if normalized_target_type != rule.target_type:
            raise ApiError(code="unsupported_agent_action_target", message="Agent action target type is not supported.", status=422)

        normalized_side_effect_level = side_effect_level or rule.side_effect_level
        if normalized_side_effect_level != rule.side_effect_level:
            raise ApiError(code="unsupported_agent_action_risk", message="Agent action side effect level is not supported.", status=422)

        return AgentActionPolicyDecision(
            action_type=rule.action_type,
            target_type=rule.target_type,
            side_effect_level=rule.side_effect_level,
            requires_confirmation=rule.requires_confirmation,
        )
