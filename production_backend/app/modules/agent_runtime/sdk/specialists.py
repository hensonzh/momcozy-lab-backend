from __future__ import annotations

from dataclasses import dataclass

from ..routing.schemas import SpecialistId
from ..tools.contracts import ToolContract
from .playbooks import (
    AFTER_SALES_PLAYBOOK,
    GENERAL_PLAYBOOK,
    LACTATION_PLAYBOOK,
    POSTPARTUM_PLAYBOOK,
    PREGNANCY_SERVICE_PLAYBOOK,
    SAFETY_PLAYBOOK,
    ServicePlaybook,
)


@dataclass(frozen=True)
class AgentSpecialistProfile:
    id: str
    display_name: str
    instructions: str
    tool_contracts: tuple[str, ...] = ()
    tool_domains: tuple[str, ...] = ()
    trigger_terms: tuple[str, ...] = ()
    prompt_version: str = "v1"
    service_playbook: ServicePlaybook | None = None
    memory_scopes: tuple[str, ...] = ()
    direct_apply_actions: tuple[str, ...] = ()
    confirmation_required_actions: tuple[str, ...] = ()
    handoff_targets: tuple[str, ...] = ()

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
                id=SpecialistId.LACTATION.value,
                display_name="Lactation specialist",
                instructions=(
                    "Use the lactation service playbook for milk status, feeding, pumping, milk-management plans, "
                    "and IBCLC handoff flows."
                ),
                service_playbook=LACTATION_PLAYBOOK,
                tool_contracts=(
                    "artifacts.lactation_summary.create",
                    "profile.read",
                    "business.context.read",
                    "records.milk_summary.read",
                    "records.milk_status.read",
                    "records.feeding_record.propose",
                    "records.pumping_record.propose",
                    "plans.milk_plan.propose",
                    "notifications.milk_reminder.propose",
                    "support.ticket.propose",
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
                    "乳",
                    "泌乳",
                    "追奶",
                    "稳奶",
                    "减奶",
                    "堵奶",
                    "涨奶",
                    "含乳",
                    "乳头疼",
                    "ibclc",
                    "哺乳顾问",
                    "泌乳顾问",
                ),
                memory_scopes=("communication_preference", "lactation_preference"),
                direct_apply_actions=(
                    "records.feeding_record.create",
                    "records.pumping_record.create",
                ),
                confirmation_required_actions=(
                    "plans.milk_plan.create",
                    "notifications.milk_reminder.create",
                ),
            ),
            AgentSpecialistProfile(
                id=SpecialistId.PREGNANCY.value,
                display_name="Pregnancy service specialist",
                instructions=(
                    "Use the pregnancy service playbook for pregnancy plans, hospital-bag cards, diary entries, "
                    "birth preparation, and labor communication cards."
                ),
                service_playbook=PREGNANCY_SERVICE_PLAYBOOK,
                tool_contracts=(
                    "artifacts.hospital_bag_card.create",
                    "artifacts.labor_communication_card.create",
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
                    "入院包",
                    "住院包",
                    "日记",
                    "分娩",
                    "生产偏好",
                    "产房沟通",
                    "分娩沟通单",
                    "给护士",
                    "陪产",
                ),
                memory_scopes=("communication_preference", "pregnancy_preference"),
                direct_apply_actions=("hospital_bag.cart_update",),
                confirmation_required_actions=(
                    "pregnancy.plan.create",
                    "plans.task.create",
                    "plans.task.complete",
                    "diary.entry.upsert",
                ),
            ),
            AgentSpecialistProfile(
                id=SpecialistId.POSTPARTUM.value,
                display_name="Postpartum recovery specialist",
                instructions=(
                    "Use the postpartum recovery playbook for gentle recovery check-ins, daily state summaries, "
                    "diary context, and recovery task planning."
                ),
                service_playbook=POSTPARTUM_PLAYBOOK,
                tool_contracts=(
                    "artifacts.postpartum_checkin.create",
                    "profile.read",
                    "business.context.read",
                    "plans.current.read",
                    "plans.task_create.propose",
                    "diary.recent.read",
                    "diary.entry_upsert.propose",
                    "records.milk_summary.read",
                ),
                trigger_terms=(
                    "postpartum",
                    "recovery",
                    "recover",
                    "pelvic",
                    "c-section",
                    "cesarean",
                    "产后",
                    "康复",
                    "恢复",
                    "剖腹产",
                    "盆底",
                    "恶露",
                ),
                memory_scopes=("communication_preference", "postpartum_preference"),
                confirmation_required_actions=("plans.task.create", "diary.entry.upsert"),
            ),
            AgentSpecialistProfile(
                id=SpecialistId.AFTER_SALES.value,
                display_name="After-sales service specialist",
                instructions=(
                    "Use the after-sales playbook for device guidance, troubleshooting, support handoff, and support "
                    "ticket proposal flows."
                ),
                service_playbook=AFTER_SALES_PLAYBOOK,
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "devices.pump_status.read",
                    "devices.guidance_assets.read",
                    "files.vision_summary.read",
                    "records.milk_summary.read",
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
                    "support",
                    "ticket",
                    "customer service",
                    "help desk",
                    "客服",
                    "工单",
                    "顾问",
                    "人工",
                ),
                memory_scopes=("communication_preference", "support_preference"),
                confirmation_required_actions=("support.ticket.create",),
                handoff_targets=("customer_service", "ibclc"),
            ),
            AgentSpecialistProfile(
                id=SpecialistId.SAFETY.value,
                display_name="Safety guardrail",
                instructions=(
                    "Use the safety playbook for emergency, health red-flag, emotional-crisis, child-safety, "
                    "permission-bypass, and prompt-injection messages."
                ),
                service_playbook=SAFETY_PLAYBOOK,
                tool_contracts=("profile.read", "business.context.read", "support.ticket.propose"),
                trigger_terms=(
                    "emergency",
                    "suicide",
                    "self harm",
                    "bleeding",
                    "fever",
                    "chest pain",
                    "呼吸困难",
                    "出血",
                    "发烧",
                    "自杀",
                    "伤害自己",
                ),
                handoff_targets=("emergency_service", "professional_support"),
            ),
            AgentSpecialistProfile(
                id=SpecialistId.GENERAL.value,
                display_name="General product assistant",
                instructions=(
                    "Use the general playbook for lightweight MomCozy assistance, navigation, clarification, and "
                    "non-sensitive memory preference capture."
                ),
                service_playbook=GENERAL_PLAYBOOK,
                tool_contracts=(
                    "profile.read",
                    "business.context.read",
                    "plans.current.read",
                    "diary.recent.read",
                    "records.milk_summary.read",
                    "devices.guidance_assets.read",
                    "memory.create.propose",
                    "support.ticket.propose",
                ),
                trigger_terms=(),
                memory_scopes=("communication_preference",),
                confirmation_required_actions=("support.ticket.create",),
            ),
        ),
        default_profile_id=SpecialistId.GENERAL.value,
    )
