from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...core.errors import ApiError


@dataclass(frozen=True)
class AgentActionPolicyDecision:
    action_type: str
    target_type: str
    side_effect_level: str
    requires_confirmation: bool
    allows_apply_payload_edit: bool


@dataclass(frozen=True)
class AgentActionPolicyRule:
    action_type: str
    target_type: str
    side_effect_level: str
    requires_confirmation: bool = True
    allows_apply_payload_edit: bool = True


class AgentActionPolicy:
    def __init__(self, *, rules: Mapping[str, AgentActionPolicyRule] | None = None) -> None:
        self.rules = dict(rules or {})

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
            allows_apply_payload_edit=rule.allows_apply_payload_edit,
        )


def action_presentation_payload(
    *,
    action: Any,
    action_policy: AgentActionPolicy | None = None,
) -> dict[str, bool | str]:
    policy = action_policy or AgentActionPolicy()
    decision = policy.validate(
        action_type=str(getattr(action, "action_type", "") or ""),
        target_type=str(getattr(action, "target_type", "") or ""),
        side_effect_level=str(getattr(action, "side_effect_level", "") or ""),
    )
    return {
        "requires_confirmation": decision.requires_confirmation,
        "confirmation_policy": "always" if decision.requires_confirmation else "explicit_intent",
        "user_visible": decision.requires_confirmation,
    }
