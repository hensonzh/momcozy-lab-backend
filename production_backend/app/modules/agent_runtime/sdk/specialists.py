from __future__ import annotations

from dataclasses import dataclass

from ..tools.contracts import ToolContract


@dataclass(frozen=True)
class AgentSpecialistProfile:
    id: str
    display_name: str
    instructions: str
    tool_contracts: tuple[str, ...] = ()
    tool_domains: tuple[str, ...] = ()
    trigger_terms: tuple[str, ...] = ()

    def allows_tool(self, contract: ToolContract) -> bool:
        if self.tool_contracts:
            return contract.name in self.tool_contracts
        return not self.tool_domains or contract.domain in self.tool_domains


class AgentSpecialistRegistry:
    def __init__(self, profiles: tuple[AgentSpecialistProfile, ...], default_profile_id: str) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        self._ordered_profiles = profiles
        self._default_profile_id = default_profile_id
        if default_profile_id not in self._profiles:
            raise ValueError("default specialist profile is not registered")

    def get(self, profile_id: str) -> AgentSpecialistProfile:
        return self._profiles[profile_id]

    def select(self, *, user_message: str) -> AgentSpecialistProfile:
        normalized = user_message.lower()
        for profile in self._ordered_profiles:
            if profile.id == self._default_profile_id:
                continue
            if any(term.lower() in normalized for term in profile.trigger_terms):
                return profile
        return self.get(self._default_profile_id)


def default_specialist_registry() -> AgentSpecialistRegistry:
    return AgentSpecialistRegistry(
        profiles=(
            AgentSpecialistProfile(
                id="milk_management",
                display_name="Milk management specialist",
                instructions=(
                    "Focus on feeding, pumping, milk trends, reminders, and milk-management plans. "
                    "Read owner-scoped records before interpreting supply or proposing changes."
                ),
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "records.milk_summary.read",
                    "records.feeding_record.propose",
                    "records.pumping_record.propose",
                    "plans.milk_plan.propose",
                    "notifications.milk_reminder.propose",
                ),
                trigger_terms=(
                    "milk",
                    "feeding",
                    "feed",
                    "pumping",
                    "pumped",
                    "pump session",
                    "supply",
                    "奶",
                    "喂",
                    "吸奶",
                    "泵奶",
                ),
            ),
            AgentSpecialistProfile(
                id="pregnancy_planning",
                display_name="Pregnancy planning specialist",
                instructions=(
                    "Focus on pregnancy plans, tasks, diary entries, due-date context, and birth preparation. "
                    "Use confirmation-first actions for plan, task, diary, and hospital-bag changes."
                ),
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "pregnancy.plan_context.read",
                    "pregnancy.plan_create.propose",
                    "plans.current.read",
                    "plans.task_create.propose",
                    "plans.task_complete.propose",
                    "diary.recent.read",
                    "diary.entry_upsert.propose",
                    "hospital_bag.cart_update.propose",
                ),
                trigger_terms=(
                    "pregnancy",
                    "pregnant",
                    "due date",
                    "birth plan",
                    "hospital bag",
                    "diary",
                    "孕",
                    "预产期",
                    "待产包",
                    "日记",
                    "分娩",
                ),
            ),
            AgentSpecialistProfile(
                id="device_support",
                display_name="Device support specialist",
                instructions=(
                    "Focus on pump device status, packaged guidance assets, troubleshooting, and support-ticket handoff. "
                    "Keep device guidance read-only unless an explicit confirmable action contract exists."
                ),
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "devices.pump_status.read",
                    "devices.guidance_assets.read",
                    "files.vision_summary.read",
                    "support.ticket.propose",
                ),
                trigger_terms=(
                    "device",
                    "air1",
                    "suction",
                    "bluetooth",
                    "ble",
                    "troubleshoot",
                    "charging",
                    "firmware",
                    "设备",
                    "蓝牙",
                    "故障",
                    "充电",
                    "吸奶器",
                ),
            ),
            AgentSpecialistProfile(
                id="support_handoff",
                display_name="Support handoff specialist",
                instructions=(
                    "Focus on support-ticket creation and professional handoff. "
                    "Do not claim that a human has been contacted until a confirmed action is applied."
                ),
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "devices.pump_status.read",
                    "records.milk_summary.read",
                    "support.ticket.propose",
                ),
                trigger_terms=(
                    "support",
                    "ticket",
                    "customer service",
                    "ibclc",
                    "consult",
                    "help desk",
                    "客服",
                    "工单",
                    "顾问",
                    "人工",
                ),
            ),
            AgentSpecialistProfile(
                id="memory_preferences",
                display_name="Memory preferences specialist",
                instructions=(
                    "Focus on stable user preferences and recurring constraints. "
                    "Sensitive health, child, crisis, or regulated facts must not be stored as memory."
                ),
                tool_contracts=("profile.read", "business.context.read", "memory.create.propose"),
                trigger_terms=(
                    "remember",
                    "preference",
                    "prefer",
                    "always remind",
                    "don't remind",
                    "记住",
                    "偏好",
                    "以后",
                    "不要提醒",
                ),
            ),
            AgentSpecialistProfile(
                id="general_product",
                display_name="General product assistant",
                instructions=(
                    "Handle general MomCozy product assistance. Select read tools before answering factual "
                    "questions and propose confirmable actions for user-visible writes."
                ),
                tool_domains=(),
                trigger_terms=(),
            ),
        ),
        default_profile_id="general_product",
    )
