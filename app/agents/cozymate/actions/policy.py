from __future__ import annotations

from typing import Mapping

from app.agent_runtime.actions.policy import AgentActionPolicy, AgentActionPolicyRule


COZYMATE_ACTION_RULES: Mapping[str, AgentActionPolicyRule] = {
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
        requires_confirmation=False,
    ),
    "profile.update": AgentActionPolicyRule(
        action_type="profile.update",
        target_type="profile",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "profile.current_infants.replace": AgentActionPolicyRule(
        action_type="profile.current_infants.replace",
        target_type="profile",
        side_effect_level="medium",
        requires_confirmation=True,
        allows_apply_payload_edit=False,
    ),
    "diary.entry.save": AgentActionPolicyRule(
        action_type="diary.entry.save",
        target_type="diary_entry",
        side_effect_level="low",
        requires_confirmation=False,
    ),
    "diary.entry.delete": AgentActionPolicyRule(
        action_type="diary.entry.delete",
        target_type="diary_entry",
        side_effect_level="medium",
        requires_confirmation=False,
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
    "records.feeding_record.update": AgentActionPolicyRule(
        action_type="records.feeding_record.update",
        target_type="feeding_record",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "records.pumping_record.update": AgentActionPolicyRule(
        action_type="records.pumping_record.update",
        target_type="pumping_record",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "records.feeding_record.delete": AgentActionPolicyRule(
        action_type="records.feeding_record.delete",
        target_type="feeding_record",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "records.pumping_record.delete": AgentActionPolicyRule(
        action_type="records.pumping_record.delete",
        target_type="pumping_record",
        side_effect_level="medium",
        requires_confirmation=False,
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
        requires_confirmation=False,
    ),
    "records.growth_record.delete": AgentActionPolicyRule(
        action_type="records.growth_record.delete",
        target_type="growth_record",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.milk_schedule.reschedule": AgentActionPolicyRule(
        action_type="plans.milk_schedule.reschedule",
        target_type="plan",
        side_effect_level="medium",
        allows_apply_payload_edit=False,
    ),
    "pregnancy.plan.create": AgentActionPolicyRule(
        action_type="pregnancy.plan.create",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.task.create": AgentActionPolicyRule(
        action_type="plans.task.create",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.task.complete": AgentActionPolicyRule(
        action_type="plans.task.complete",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.task.update": AgentActionPolicyRule(
        action_type="plans.task.update",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.task.delete": AgentActionPolicyRule(
        action_type="plans.task.delete",
        target_type="plan_task",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.plan.update": AgentActionPolicyRule(
        action_type="plans.plan.update",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
    "plans.plan.delete": AgentActionPolicyRule(
        action_type="plans.plan.delete",
        target_type="plan",
        side_effect_level="medium",
        requires_confirmation=False,
    ),
}


def cozymate_action_policy() -> AgentActionPolicy:
    return AgentActionPolicy(rules=COZYMATE_ACTION_RULES)
