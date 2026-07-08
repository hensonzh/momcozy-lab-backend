from __future__ import annotations

from dataclasses import dataclass

from ....core.errors import ApiError
from ..routing.schemas import RoutingPlan, ServiceSkillId


@dataclass(frozen=True)
class ToolGroup:
    id: str
    service_skill_id: ServiceSkillId | None
    description: str
    tool_contracts: tuple[str, ...]


class ToolGroupRegistry:
    def __init__(self, groups: tuple[ToolGroup, ...]) -> None:
        self._groups = {group.id: group for group in groups}
        if len(self._groups) != len(groups):
            raise ValueError("duplicate agent tool group id")

    def get(self, group_id: str) -> ToolGroup:
        group = self._groups.get(group_id)
        if group is None:
            raise ApiError(code="agent_tool_group_not_found", message="Agent tool group is not registered.", status=500)
        return group

    def list(self) -> tuple[ToolGroup, ...]:
        return tuple(self._groups.values())

    def tool_names_for_plan(self, plan: RoutingPlan) -> tuple[str, ...]:
        names: set[str] = set()
        for group_id in plan.tool_group_ids:
            group = self.get(group_id)
            if group.service_skill_id is not None and group.service_skill_id != plan.selected_skill_id:
                raise ApiError(
                    code="agent_tool_group_scope_mismatch",
                    message="Agent tool group does not belong to the selected service skill.",
                    status=500,
                )
            names.update(group.tool_contracts)
        return tuple(sorted(names))


def default_tool_group_registry() -> ToolGroupRegistry:
    return ToolGroupRegistry(
        groups=(
            ToolGroup(
                id="general.base",
                service_skill_id=None,
                description="所有服务技能可用的当前用户基础读取工具。",
                tool_contracts=("profile.read", "business.context.read"),
            ),
            ToolGroup(
                id="general.memory",
                service_skill_id=None,
                description="用户明确要求记住非敏感偏好时使用。",
                tool_contracts=("memory.create.propose",),
            ),
            ToolGroup(
                id="general.support",
                service_skill_id=None,
                description="通用问题需要售后或人工支持时使用。",
                tool_contracts=("support.ticket.propose",),
            ),
            ToolGroup(
                id="pregnancy.context",
                service_skill_id=ServiceSkillId.BIRTH_PREP,
                description="孕期计划、待产包和日记相关上下文读取。",
                tool_contracts=("pregnancy.plan_context.read", "plans.current.read", "diary.recent.read"),
            ),
            ToolGroup(
                id="pregnancy.plan",
                service_skill_id=ServiceSkillId.BIRTH_PREP,
                description="创建或推进孕期计划和任务。",
                tool_contracts=("birth_journey_plan_card_create", "plans.task_create.propose", "plans.task_complete.propose"),
            ),
            ToolGroup(
                id="pregnancy.diary",
                service_skill_id=ServiceSkillId.BIRTH_PREP,
                description="写入或更新孕期日记。",
                tool_contracts=("diary.entry_upsert.propose",),
            ),
            ToolGroup(
                id="pregnancy.hospital_bag",
                service_skill_id=ServiceSkillId.BIRTH_PREP,
                description="创建待产包卡片或调整待产包购物车。",
                tool_contracts=("hospital_bag_form_create", "hospital_bag_card_create", "hospital_bag_cart_update", "hospital_bag_pump_recommend"),
            ),
            ToolGroup(
                id="pregnancy.labor_communication",
                service_skill_id=ServiceSkillId.BIRTH_PREP,
                description="创建分娩沟通单。",
                tool_contracts=("birth_plan_form_create", "labor_communication_card_create"),
            ),
            ToolGroup(
                id="lactation.milk_read",
                service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
                description="读取奶量、喂养和吸奶状态。",
                tool_contracts=("records.milk_status.read", "records.milk_summary.read"),
            ),
            ToolGroup(
                id="lactation.record_write",
                service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
                description="补录真实喂养或吸奶记录。",
                tool_contracts=("records.feeding_record.propose", "records.pumping_record.propose"),
            ),
            ToolGroup(
                id="lactation.plan",
                service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
                description="提出追奶、稳奶、减奶计划或奶量提醒。",
                tool_contracts=("plans.milk_plan.propose", "notifications.milk_reminder.propose"),
            ),
            ToolGroup(
                id="lactation.artifact",
                service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
                description="奶量管理当前没有独立 artifact 工具；使用奶量工具返回的结构化分析。",
                tool_contracts=(),
            ),
            ToolGroup(
                id="lactation.handoff",
                service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
                description="需要 IBCLC 或人工支持时整理工单。",
                tool_contracts=("support.ticket.propose",),
            ),
            ToolGroup(
                id="postpartum.context",
                service_skill_id=ServiceSkillId.HEALTH_CONSULTATION,
                description="读取产后恢复相关任务、日记和奶量上下文。",
                tool_contracts=("plans.current.read", "diary.recent.read", "records.milk_summary.read"),
            ),
            ToolGroup(
                id="postpartum.checkin",
                service_skill_id=ServiceSkillId.HEALTH_CONSULTATION,
                description="健康咨询当前没有独立恢复打卡 artifact 工具；使用问答与任务工具。",
                tool_contracts=(),
            ),
            ToolGroup(
                id="postpartum.task",
                service_skill_id=ServiceSkillId.HEALTH_CONSULTATION,
                description="提出产后恢复任务或日记写入。",
                tool_contracts=("plans.task_create.propose", "diary.entry_upsert.propose"),
            ),
            ToolGroup(
                id="after_sales.device_guidance",
                service_skill_id=ServiceSkillId.DEVICE_GUIDANCE,
                description="设备状态读取、官方素材读取和图片摘要。",
                tool_contracts=("devices.pump_status.read", "devices.guidance_assets.read", "files.vision_summary.read"),
            ),
            ToolGroup(
                id="after_sales.support",
                service_skill_id=ServiceSkillId.DEVICE_GUIDANCE,
                description="创建售后工单提案。",
                tool_contracts=("support.ticket.propose",),
            ),
            ToolGroup(
                id="safety.support",
                service_skill_id=None,
                description="安全处理后需要后续支持时整理工单。",
                tool_contracts=("support.ticket.propose",),
            ),
        )
    )
