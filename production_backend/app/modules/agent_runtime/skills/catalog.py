from __future__ import annotations

from dataclasses import dataclass

from ..routing.schemas import SpecialistId
from ..sdk.specialists import AgentSpecialistProfile, AgentSpecialistRegistry, default_specialist_registry
from .playbooks import ServicePlaybook


@dataclass(frozen=True)
class AgentServiceSkill:
    id: str
    display_name: str
    instructions: str
    service_playbook: ServicePlaybook
    tool_contracts: tuple[str, ...]
    trigger_terms: tuple[str, ...]
    prompt_version: str
    memory_scopes: tuple[str, ...]
    direct_apply_actions: tuple[str, ...]
    confirmation_required_actions: tuple[str, ...]
    handoff_targets: tuple[str, ...]

    @classmethod
    def from_specialist(cls, profile: AgentSpecialistProfile) -> "AgentServiceSkill":
        if profile.service_playbook is None:
            raise ValueError(f"Agent service skill {profile.id} must have a service playbook")
        return cls(
            id=profile.id,
            display_name=profile.display_name,
            instructions=profile.instructions,
            service_playbook=profile.service_playbook,
            tool_contracts=profile.tool_contracts,
            trigger_terms=profile.trigger_terms,
            prompt_version=profile.prompt_version,
            memory_scopes=profile.memory_scopes,
            direct_apply_actions=profile.direct_apply_actions,
            confirmation_required_actions=profile.confirmation_required_actions,
            handoff_targets=profile.handoff_targets,
        )

    def prompt_block(self) -> str:
        action_lines = [
            *[f"- 自动执行 action：{action}" for action in self.direct_apply_actions],
            *[f"- 需要确认 action：{action}" for action in self.confirmation_required_actions],
        ]
        if not action_lines:
            action_lines = ["- 无场景专属写入 action。"]
        return "\n".join(
            [
                f"# 服务 Skill {self.id} ({self.prompt_version})",
                f"展示名称：{self.display_name}",
                f"运行说明：{self.instructions}",
                self.service_playbook.prompt_block(),
                "## 可用工具",
                *[f"- {tool_name}" for tool_name in self.tool_contracts],
                "## Action 策略",
                *action_lines,
            ]
        )

    def state_summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "prompt_version": self.prompt_version,
            "service_playbook": self.service_playbook.state_summary(),
            "tool_contracts": list(self.tool_contracts),
            "direct_apply_actions": list(self.direct_apply_actions),
            "confirmation_required_actions": list(self.confirmation_required_actions),
        }


class AgentServiceSkillRegistry:
    def __init__(self, skills: tuple[AgentServiceSkill, ...], default_skill_id: str) -> None:
        self._skills = {skill.id: skill for skill in skills}
        self._ordered_skills = skills
        self._default_skill_id = default_skill_id
        if default_skill_id not in self._skills:
            raise ValueError("default agent service skill is not registered")

    def get(self, skill_id: str) -> AgentServiceSkill:
        return self._skills[skill_id]

    def list(self) -> tuple[AgentServiceSkill, ...]:
        return self._ordered_skills

    def default(self) -> AgentServiceSkill:
        return self.get(self._default_skill_id)


def default_service_skill_registry(
    specialist_registry: AgentSpecialistRegistry | None = None,
) -> AgentServiceSkillRegistry:
    resolved_registry = specialist_registry or default_specialist_registry()
    return AgentServiceSkillRegistry(
        skills=tuple(AgentServiceSkill.from_specialist(profile) for profile in resolved_registry.list()),
        default_skill_id=SpecialistId.GENERAL.value,
    )
