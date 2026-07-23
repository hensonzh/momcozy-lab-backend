# Momcozy 多智能体架构

## 总体架构

系统采用一个主智能体和产前、泌乳、设备三个专业子智能体，所有智能体共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 回复与路由

主智能体负责通用回答、专业路由和多专业结果汇总，单一专业任务由对应子智能体直接回复。

主智能体只持有公共 Tool、有界专业能力 Tool 和三个 Handoff，不持有子智能体的完整专业指令与底层 Tool。

## 专业能力装配

- 产前、泌乳和设备三个子智能体在创建时静态绑定各自的完整专业指令与领域 Tool Allowlist。
- 各子智能体 Allowlist 中的 Tool 均作为顶层函数工具直接暴露，不使用 Namespace、deferred loading 或 `tool_search`。
- 主智能体只根据 Handoff 的名称、描述和输入契约进行路由，不通过模型可见 Tool 动态获取专业指令。
- 业务事实通过 owner-scoped Read Tool 或内部 Context Projector 按需读取，不与专业指令一起装载。
- 上下文账本记录 Handoff、Tool 调用和结果，不维护“已加载专业 Skill”状态。
- `service_skill_id` 仅用于 run 归属、评测和观测，不代表可调用的动态加载能力。

## 场景归属

| 归属 | 场景 |
| --- | --- |
| 主智能体 | 通用问答、健康咨询、普通情绪支持、孕期日记、用户资料、通用计划任务、产后恢复问答、范围澄清和多意图汇总 |
| 产前服务智能体 | 孕期计划、孕期计划任务、待产包、待产包购物车、分娩沟通单和事项型孕期焦虑 |
| 泌乳服务智能体 | 奶量摘要与分析、泌乳计划、日程调整、喂养/吸奶/生长记录、吸奶小结、提醒和 IBCLC 衔接 |
| 设备服务智能体 | 吸奶器选型、开箱使用、清洁消毒、蓝牙与法兰指导、故障排查和售后工单 |
| 全局能力 | 健康红旗、情绪危机、设备安全、权限、Prompt 防护、上下文账本和 Memory |

## Tool 归属（45）

当前共有 45 个模型可见 Tool Contract。Tool 只有一个领域归属，但公共 Tool 和有界专业能力可以按 Allowlist 暴露给多个智能体。

### 主智能体与公共能力（13）

`profile_read`、`profile_update`、`plans_current_read`、`plans_calendar_read`、`plans_task_create_propose`、`plans_task_complete_propose`、`plans_task_update_propose`、`plans_task_delete_propose`、`plans_plan_delete_propose`、`pregnancy_diary_query`、`pregnancy_diary_save`、`pregnancy_diary_delete`、`conversation_history_image_load`。

### 产前服务智能体（10）

`pregnancy_plan_intake_start`、`pregnancy_plan_intake_analyze`、`pregnancy_plan_intake_advance`、`pregnancy_plan_propose`、`pregnancy_plan_todo_propose`、`birth_plan_form_create`、`labor_communication_card_create`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_cart_update`。

### 泌乳服务智能体（19）

`records_milk_summary_read`、`records_milk_status_read`、`records_milk_analysis_read`、`records_milk_analysis_intake`、`records_milk_analysis_evaluate`、`records_growth_read`、`plans_milk_plan_propose`、`plans_milk_schedule_propose`、`plans_milk_task_update_propose`、`plans_milk_task_delete_propose`、`notifications_milk_reminder_propose`、`records_feeding_record_propose`、`records_feeding_record_delete_propose`、`records_pumping_record_propose`、`records_pumping_record_delete_propose`、`records_growth_record_propose`、`records_growth_record_update_propose`、`records_growth_record_delete_propose`、`ibclc_consult_card_create`。

### 设备服务智能体（3）

`devices_guidance`、`hospital_bag_pump_recommend`、`support_ticket_propose`。

以上 3 个 Tool 全部直接暴露给设备服务智能体。`devices_guidance` 同时负责按主题读取官方资料和维护一步步开箱流程。

#### `devices_guidance` 统一契约

必填参数为已确认的 `model` 和 `operation`。当前支持以下操作：

| `operation` | 用途 | 额外参数 | 是否修改流程 |
| --- | --- | --- | --- |
| `read` | 按需读取某一说明书主题或明确步骤 | `topic`、`step` 二选一；可选 `resource_kind`；法兰主题可传 `measured_nipple_mm` | 否 |
| `start_or_resume` | 开始新开箱指导，或恢复当前线程的已有进度 | 无 | 是 |
| `complete_current` | 在用户明确完成当前步骤后，只推进一个主步骤 | 无 | 是 |
| `cancel` | 取消当前开箱指导 | 无 | 是 |

工具统一返回 `device-guidance.result.v1`，包含 `status`、`mode`、`device_model`、`document_version`、`guidance` 和 `workflow`。直接查询的 `mode=direct`、`workflow=null`；连续指导的 `mode=walkthrough`，以 `workflow.current_step` 作为唯一当前步骤。

连续指导沿用内部 `device_unboxing` Workflow。`start_or_resume` 必须幂等恢复已有进度，`complete_current` 每次只持久化推进一步；流程中的临时清洗、蓝牙或当前步骤问题使用 `read`，不得改变 `active_step` 或 `completed_steps`。

## 待调整项

`hospital_bag_pump_recommend` 迁入设备领域并改为设备语义名称。

## Tool 之外的能力

`health-consultation` 和 `emotion-support` 是主智能体静态能力，不是模型可见 Tool；`business_context_read` 与 `pregnancy_plan_context_read` 是内部 Handler，Health Web Search 是 Provider 能力。

## 跨域边界

| 用户目标 | 归属 |
| --- | --- |
| 孕期事项、准备顺序和待产安排 | 产前服务智能体 |
| 孕期症状、检查、用药和就医判断 | 主智能体健康能力 |
| 奶量、摄入、喂养记录和泌乳计划 | 泌乳服务智能体 |
| 发热、红肿和明显疼痛等身体风险 | 主智能体健康能力优先 |
| 法兰型号、安装、吸力和漏气 | 设备服务智能体 |
| 乳头受伤或明显疼痛 | 主智能体健康能力优先 |
| 待产包商品调整 | 产前服务智能体 |
| 吸奶器型号推荐和比较 | 设备服务智能体 |

## 全局安全

健康、情绪、人身、设备和权限安全策略对所有智能体生效，并可优先中断普通业务流程。
