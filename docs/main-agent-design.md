# Momcozy 多智能体架构

## 总体架构

系统采用一个主智能体和产前、泌乳、设备三个专业子智能体，所有智能体共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 回复与路由

主智能体负责通用回答、专业路由和多专业结果汇总，单一专业任务由对应子智能体直接回复。

主智能体只持有公共 Tool、有界专业能力 Tool 和三个 Handoff，不持有子智能体的完整专业指令与底层 Tool。

## 场景归属

| 归属 | 场景 |
| --- | --- |
| 主智能体 | 通用问答、健康咨询、普通情绪支持、孕期日记、用户资料、通用计划任务、产后恢复问答、范围澄清和多意图汇总 |
| 产前服务智能体 | 孕期计划、孕期计划任务、待产包、待产包购物车和事项型孕期焦虑 |
| 泌乳服务智能体 | 奶量摘要与分析、泌乳计划、日程调整、喂养/吸奶/生长记录、吸奶小结、提醒和 IBCLC 衔接 |
| 设备服务智能体 | 吸奶器选型、设备状态、开箱使用、清洁消毒、蓝牙与法兰指导、故障排查和售后工单 |
| 全局能力 | 健康红旗、情绪危机、设备安全、权限、Prompt 防护、上下文账本和 Memory |

## Tool 归属

Tool 只有一个领域归属，但公共 Tool 和有界专业能力可以按 Allowlist 暴露给多个智能体。

### 主智能体与公共能力（13）

`profile_read`、`profile_update`、`plans_current_read`、`plans_calendar_read`、`plans_task_create_propose`、`plans_task_complete_propose`、`plans_task_update_propose`、`plans_task_delete_propose`、`plans_plan_delete_propose`、`pregnancy_diary_query`、`pregnancy_diary_save`、`pregnancy_diary_delete`、`conversation_history_image_load`

### 产前服务智能体（8）

`pregnancy_plan_intake_start`、`pregnancy_plan_intake_analyze`、`pregnancy_plan_intake_advance`、`pregnancy_plan_propose`、`pregnancy_plan_todo_propose`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_cart_update`。

### 泌乳服务智能体（19）

`records_milk_summary_read`、`records_milk_status_read`、`records_milk_analysis_read`、`records_milk_analysis_intake`、`records_milk_analysis_evaluate`、`records_growth_read`、`plans_milk_plan_propose`、`plans_milk_schedule_propose`、`plans_milk_task_update_propose`、`plans_milk_task_delete_propose`、`notifications_milk_reminder_propose`、`records_feeding_record_propose`、`records_feeding_record_delete_propose`、`records_pumping_record_propose`、`records_pumping_record_delete_propose`、`records_growth_record_propose`、`records_growth_record_update_propose`、`records_growth_record_delete_propose`、`ibclc_consult_card_create`。

### 设备服务智能体（5）

`devices_pump_status_read`、`devices_guidance_read`、`devices_unboxing_advance`、`hospital_bag_pump_recommend`、`support_ticket_propose`。

### 已移除（2）

`birth_plan_form_create`、`labor_communication_card_create` 及分娩沟通单表单、卡片、Fact、Skill 和 Eval 链路已删除。

### 重构后移除（1）

`load_service_skill` 由三个 Handoff 和各智能体静态专业定义取代。

## 调整项

`hospital_bag_pump_recommend` 迁入设备领域并改为设备语义名称，通用计划 Tool 从现有专业 Namespace 移入公共能力面。

## `propose` 命名待统一

当前 `_propose` 只表示向 Action Runtime 提交结构化变更意图，不能表示是否直接写入；实际行为以 `ActionPolicy.requires_confirmation` 为准。

| 当前行为 | 当前 Tool | 合并后命名规则 |
| --- | --- | --- |
| 生成方案，等待用户确认后写入 | `plans_milk_plan_propose`、`plans_milk_schedule_propose`、`notifications_milk_reminder_propose` | 保留 `_propose` |
| 用户已明确授权，调用后直接写入 | 其余 Action-backed `_propose` Tool | 移除 `_propose`，使用实际动作结尾：`_create`、`_update`、`_delete`、`_complete` |
| 只生成 Agent 内部草稿，不创建业务资源 | `support_ticket_propose` | 改为 `support_ticket_draft_create` |

本分支暂不修改 Tool 名；与其他分支合并后，再全量同步 Registry、Schema、Handler、Policy、Prompt、测试、Eval 和客户端事件 Fixture。

## Tool 之外的能力

`health-consultation` 和 `emotion-support` 当前是 Skill，`business_context_read` 与 `pregnancy_plan_context_read` 是内部 Handler，Health Web Search 是 Provider 能力。

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
