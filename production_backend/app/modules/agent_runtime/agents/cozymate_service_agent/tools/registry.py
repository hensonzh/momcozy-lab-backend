from __future__ import annotations

from typing import Any

from production_backend.app.core.errors import ApiError

from .contracts import ToolContract
from .schemas import input_schema_for_tool


class ToolContractRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        if contract.name in self._contracts:
            raise ApiError(code="conflict", message="Tool contract is already registered.", status=409)
        self._contracts[contract.name] = contract

    def get(self, name: str) -> ToolContract:
        contract = self._contracts.get(name)
        if contract is None:
            raise ApiError(code="not_found", message="Tool contract not found.", status=404)
        return contract

    def list(self) -> list[ToolContract]:
        return list(self._contracts.values())

    def names_for_sdk(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def eager_names(self) -> tuple[str, ...]:
        return tuple(sorted(contract.name for contract in self._contracts.values() if contract.loading_mode == "eager"))

    def deferred_names(self) -> tuple[str, ...]:
        return tuple(sorted(contract.name for contract in self._contracts.values() if contract.loading_mode == "deferred"))


def _tool_contract(*, name: str, **values: Any) -> ToolContract:
    return ToolContract(name=name, input_schema=input_schema_for_tool(name), **values)


def default_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        _tool_contract(
            name="load_service_skill",
            domain="agent_runtime",
            description=(
                "按 service_skill_id 加载一个 MomCozy 服务技能。"
                "用户当前请求需要进入奶量、产前准备、健康咨询、情绪支持或设备指导流程，且对应技能尚未驻留时调用；"
                "返回该技能说明、建议工具和小型业务事实包。仅涉及孕期日记时不要调用，直接使用全局 pregnancy_diary.manage。"
            ),
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=20,
        )
    )
    registry.register(
        _tool_contract(
            name="profile.read",
            domain="profiles",
            description="读取当前用户及宝宝的基础资料投影。用户的问题或后续动作需要核对姓名、年龄、孕产状态或宝宝资料时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="profile_update",
            domain="global",
            description="更新当前用户的基础资料。用户明确提供或更正姓名、年龄或 onboarding 状态时调用。",
            loading_mode="eager",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="records.milk_summary.read",
            domain="records",
            description="读取当前用户近期喂养、吸奶和趋势记录的简要摘要。用户要回顾近期记录或需要奶量管理概览时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="records.milk_status.read",
            domain="records",
            description="读取当前用户近期奶量状态的确定性快照。用户询问当前奶量表现、近期是否有记录或今日状态时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="records.milk_analysis.read",
            domain="records",
            description="读取当前用户近期奶量、宝宝生长和趋势事实并生成分析快照。用户要分析奶量变化、趋势或与生长记录的关系时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="records.milk_analysis.intake",
            domain="records",
            description=(
                "用户要求完整分析奶量，或回答上一轮奶量分析问题时调用；开始、恢复或推进当前线程的六项采集。"
                "回答时用 observed_answers 标注本轮原话明确覆盖的全部采集项，未明确回答的项目继续按 current_field 逐项追问，直到 can_evaluate 为 true。"
            ),
            loading_mode="eager",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.milk_analysis.evaluate",
            domain="records",
            description=(
                "用户已完成六项奶量采集且 intake 返回 can_evaluate=true 时调用；做确定性评估并生成奶量分析卡、上下文指纹和计划准入结论。"
            ),
            loading_mode="eager",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.growth.read",
            domain="records",
            description="读取当前用户宝宝的身高、体重和头围等生长记录。用户要查看宝宝近期生长数据或趋势时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.current.read",
            domain="plans",
            description="读取当前用户生效中的计划和近期任务摘要。用户要查看当前计划、待办或后续安排时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.calendar.read",
            domain="plans",
            description="按日期和状态读取当前用户的计划任务日程。用户询问某天安排、待完成事项或任务状态时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy_diary.manage",
            domain="pregnancy_diary",
            description=(
                "按日期读取、列出、写入、更新或删除当前用户的孕期日记，用户需要处理孕期日记时调用。"
                "write/update 必须传 capture_mode。用户本轮明确要求记录时传 explicit_request，并用 capture_evidence 逐字引用当前消息中的保存请求；"
                "只有 working_context.pregnancy_diary.auto_capture_enabled=true 时，才可因普通叙述传 automatic 自动记录。未授权时不要写入，可友好提示用户在孕期日记中开启自动记录。"
                "正文只保存用户明确表达的事实和感受，不写入模型建议、安抚、风险判断、医疗提醒、观察计划或诊断。"
                "write 遇到同日记录会返回 entry_already_exists 和旧日记正文；此时不能说已经保存，必须结合旧正文与本轮新增事实重新组织完整正文，并用相同 capture_mode 和证据继续调用 action=update。"
                "update 只接受整合后的完整正文，不能追加‘补充’或只传增量。"
                "delete 仅在日期明确且用户已确认时传 confirmed=true，并用 confirmation_evidence 逐字引用当前用户消息中表达删除确认的短原文；运行时会校验证据。"
            ),
            loading_mode="eager",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="devices.pump_status.read",
            domain="devices",
            description="读取当前用户吸奶器设备及近期遥测状态摘要。用户询问设备连接、在线状态、固件或近期运行状态时调用。",
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="devices.guidance.read",
            domain="devices",
            description=(
                "按设备型号、主题或明确步骤读取版本化的 Momcozy 官方说明书、FAQ、图片、PDF 和视频素材。"
                "用户需要安装、开箱、使用、清洁、排查问题，或追问某个官方步骤时调用。"
            ),
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="devices.unboxing.advance",
            domain="devices",
            description=(
                "开始、恢复、推进或取消当前线程中的设备开箱分步指导，并返回当前或下一步骤的官方资料。"
                "用户明确要一步步开箱，或确认当前主步骤已经完成时调用；不要仅凭含糊的‘好了’跳过尚未核对的子动作。"
            ),
            loading_mode="eager",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="conversation_history.image.load",
            domain="images",
            description=(
                "将当前可见对话历史中由智能体回复展示过的一张图片重新加载到本轮模型上下文。"
                "当用户追问此前智能体回复里的某张图片内容，需要基于该历史图片进行视觉理解时调用。"
            ),
            loading_mode="eager",
            read_or_write="read",
            side_effect_level="none",
            blocking_policy="must_wait",
            result_dependency="next_tool_call",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=False,
            timeout_seconds=10,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.milk_plan.propose",
            domain="plans",
            description=(
                "奶量评估允许制定计划且用户同意推荐方向时调用；模型只提交方向和用户明确给出的周期、目标或时间约束，"
                "runtime 负责生成完整计划与任务。未来已有奶量任务时，先让用户明确选择追加或替换。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.milk_schedule.propose",
            domain="plans",
            description=(
                "用户明确要求新增会议、外出等生活事项或避开已有不可用时段时调用；读取当前奶量计划并生成重排预览。"
                "本轮新增事项放入 calendar_events，runtime 会在确认后将事项与奶量任务调整原子写入。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy.plan_intake.start",
            domain="birth_prep",
            description=(
                "为当前用户创建孕期计划基础信息表单。用户同意开始制定孕期计划且当前没有 active 孕期计划时调用；"
                "不要先在聊天里逐项收集表单字段。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy.plan_intake.analyze",
            domain="birth_prep",
            description=(
                "分析应用侧已校验的孕期计划基础表单提交。当前用户消息包含 birth_journey_basic_info_intake 提交时调用，"
                "工具参数保持空对象；工具会按风险和信息缺口进入最多三轮个性化追问，或直接进入产检资料步骤。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy.plan_intake.advance",
            domain="birth_prep",
            description=(
                "推进当前用户已验证的孕期计划 intake，用户回答当前可见步骤时调用。只提交一项回答：个性化追问、"
                "孕早期产检确认、产检资料上传/跳过或最终补充；不要在参数中伪造 workflow 或附件。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy.plan.propose",
            domain="plans",
            description=(
                "基于当前用户已完成个性化追问、产检资料步骤和最终补充确认的可信 intake 创建孕期计划。"
                "只在 pregnancy.plan_intake.advance 返回 ready_to_generate 时调用。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.task_create.propose",
            domain="plans",
            description="为当前用户创建一个明确的单项计划任务。用户已明确单项内容和归属时调用并同步执行；范围含糊或批量新增时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.task_complete.propose",
            domain="plans",
            description="把当前用户唯一指定的单项计划任务标记为完成或取消完成。用户明确表达状态且 trusted task target 唯一时调用并同步执行。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="pregnancy.plan_todo.propose",
            domain="plans",
            description=(
                "更新当前用户 active 孕期计划卡片中唯一指定事项的完成状态。用户明确表示某项已完成或取消完成，"
                "并且 trusted pregnancy plan context 能唯一提供 plan_id、item_id 和 version 时调用并同步执行。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.task_update.propose",
            domain="plans",
            description="同步修改当前用户唯一指定的单项计划任务。用户明确要求调整且 trusted task target 唯一时调用；目标含糊或批量修改时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.task_delete.propose",
            domain="plans",
            description="软删除当前用户唯一指定的单项计划任务。用户明确要求删除且 trusted task target 唯一时调用并同步执行；目标含糊时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.milk_task_update.propose",
            domain="plans",
            description="用户明确要求调整唯一奶量计划任务且目标已确定时调用；目标含糊或批量修改时先澄清，批量避开日程使用 plans.milk_schedule.propose。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.milk_task_delete.propose",
            domain="plans",
            description="软删除当前用户唯一指定的奶量计划任务。用户明确要求删除且目标唯一时调用。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="plans.plan_delete.propose",
            domain="plans",
            description=(
                "删除当前用户唯一指定的整个已有计划。用户当前已明确表达删除意图且 trusted owner-scoped plan target 精确时"
                "调用并立即同步执行，不要再追加口头确认或通用确认卡；仅当目标含糊时先澄清并且不得调用。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="notifications.milk_reminder.propose",
            domain="notifications",
            description="为当前用户创建奶量、喂养或吸奶提醒并等待确认。用户明确要求在指定时间收到相关提醒时调用。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="wait_for_confirmation",
            result_dependency="none",
            requires_confirmation=True,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.feeding_record.propose",
            domain="records",
            description="保存当前用户明确提供的一次喂养记录。用户要求记录喂养时间、方式或奶量时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.pumping_record.propose",
            domain="records",
            description="保存当前用户明确提供的一次吸奶记录。用户要求记录吸奶时间、时长、档位或奶量时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.feeding_record_delete.propose",
            domain="records",
            description="软删除当前用户唯一指定的一次喂养记录。用户明确要求且 trusted record target 唯一时调用并同步执行；目标含糊时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.pumping_record_delete.propose",
            domain="records",
            description="软删除当前用户唯一指定的一次吸奶记录。用户明确要求且 trusted record target 唯一时调用并同步执行；目标含糊时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.growth_record.propose",
            domain="records",
            description="保存当前用户明确提供的一次宝宝生长记录。用户要求记录宝宝身高、体重或头围时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.growth_record_update.propose",
            domain="records",
            description="同步修改当前用户宝宝唯一指定的一次生长记录。用户明确要求更正且 trusted record target 唯一时调用；目标含糊时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="records.growth_record_delete.propose",
            domain="records",
            description="软删除当前用户宝宝唯一指定的一次生长记录。用户明确要求且 trusted record target 唯一时调用并同步执行；目标含糊时先澄清。",
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="birth_plan_form_create",
            domain="birth_prep",
            description="创建分娩沟通单信息采集表单，字段和预填信息由 runtime 生成。用户希望开始梳理分娩偏好或准备分娩沟通单时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="labor_communication_card_create",
            domain="birth_prep",
            description="根据应用侧可信的 birth_plan_card_intake 表单提交生成可渲染的分娩沟通单。用户完成信息采集并要求生成沟通单时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="hospital_bag_form_create",
            domain="hospital_bag",
            description="创建待产包信息采集表单，并由 runtime 合并可信资料和当前孕期计划。用户确认开始整理待产包时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="hospital_bag_card_create",
            domain="hospital_bag",
            description="根据应用侧可信的 hospital_bag_intake 表单提交生成可渲染的待产包清单。用户完成信息采集并要求生成待产包清单时调用。",
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="hospital_bag_cart_update",
            domain="hospital_bag",
            description=(
                "修改当前用户已有的待产包购物车并返回前端可应用的更新。"
                "用户在待产包购物车场景中要求调整预算、增删商品、修改数量、标记已有物品、恢复默认或同步已推荐吸奶器时调用；"
                "用户给出明确预算金额时使用 optimize_budget 和 target_budget。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="hospital_bag_pump_recommend",
            domain="hospital_bag",
            description=(
                "根据当前用户的预算、使用场景和偏好，从 Momcozy 官方目录推荐吸奶器型号并返回购物车同步建议。"
                "用户在购买前询问适合的型号、型号差异、预算内选择、价格或点名某型号时调用。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=False,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="support.ticket.propose",
            domain="support",
            description=(
                "用户明确同意创建售后工单时调用，用于整理可编辑的 Momcozy 售后信息表。"
                "表单由用户确认并直接提交，不再发起第二次 action 确认。"
            ),
            read_or_write="write",
            side_effect_level="medium",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    registry.register(
        _tool_contract(
            name="ibclc_consult_card_create",
            domain="health_consultation",
            description=(
                "当前用户明确要求联系/打开 IBCLC 哺乳顾问咨询，或明确确认上一轮 IBCLC 推荐时调用，创建咨询入口卡片。"
                "仅询问 IBCLC 是什么、是否需要顾问、否定/暂缓意图或无上一轮推荐的孤立确认都不得创建。"
            ),
            read_or_write="write",
            side_effect_level="low",
            blocking_policy="must_wait",
            result_dependency="final_response",
            requires_confirmation=False,
            idempotency_required=True,
            audit_required=True,
            timeout_seconds=15,
        )
    )
    return registry
