# Momcozy 多智能体架构

## 总体架构

系统采用一个主智能体和产前、泌乳、设备三个专业子智能体，所有智能体共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 实施约束

当前方案仅发布在测试环境，尚无需要保留的正式线上用户数据；少量内部测试用户及其数据后续可直接清理。因此方案和实现默认直接切换，不兼容旧架构、旧契约或既有测试数据，不保留双写、兼容分支和历史数据迁移逻辑。

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

### 产前服务智能体（4）

`pregnancy_plan_workflow`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_cart_update`。

其中 `pregnancy_plan_workflow` 是唯一对模型和 App 暴露的孕期计划流程入口，孕期计划采集、表单分析、追问推进和计划生成由该工作流内部 Handler 执行。

## 孕期计划 Workflow Contract

`pregnancy_plan_workflow` 每次只执行一个命令：

`start_or_resume`、`submit_form`、`answer_current`、`edit_answer`、`pause`、`resume`、`abandon`、`generate_plan`。

运行时以用户维度持久化唯一的活动孕期计划，状态不设自动过期时间。每次转换都增加 `revision`、更新一次性 `step_token`，并写入 append-only workflow event；因此新会话和 App 重启后仍可在已完成步骤上继续，也可以修改历史回答并使依赖它的后续步骤失效后重算。

每轮实际使用的消息、成对 Tool call/output、上下文条目引用、裁剪策略和有界 workflow 投影会写入 model-context snapshot，workflow 转换的命令、交互摘要、revision 和失效步骤则留在 append-only event ledger，便于按当时输入重放和审计。完整历史不会在后续每轮重复塞回模型：默认最多选择 64 个上下文条目、约 12,000 token，并为所有活动 workflow 单独保留约 1,200 token 的投影预算。

工作流回复分成两个独立部分：

- `workflow_reply`：仅含工作流 ID、类型、revision 和不透明 step token，用于拒绝重复点击及过期页面提交。
- `workflow_prompt`：仅含当前问题、稳定选项 ID、是否允许当前步骤的专用文字补充、可编辑步骤和允许命令，供 App 渲染，不包含内部状态 ID、token、模型指令或完整状态。

App 的选项点击、表单提交、暂停、恢复和历史修改通过 `pregnancy_plan_command.v1` 结构化指令进入确定性执行路径，不调用模型。普通输入框不附带孕期计划游标，继续作为自由对话；运行时会把有界工作流摘要作为权威上下文提供给模型，使其回答旁支问题但不推进流程，并在回复后重新返回最新 `workflow_prompt`。用户需要针对当前问题自由补充时，由工作流卡片内的专用输入框显式提交，避免把普通旁支问题误记为流程答案。

检测到需要优先处理的医疗安全信号时，安全回复覆盖普通计划回复，workflow 保留当前步骤并进入暂停态；用户后续显式恢复时继续原步骤，不把已采集内容标成失败或清空。

最终 `generate_plan` 仍经过 `pregnancy.plan.create` Action 边界；只有 `write_succeeded=true` 才能声称已生成并同步。

### 泌乳服务智能体（19）

`records_milk_summary_read`、`records_milk_status_read`、`records_milk_analysis_read`、`records_milk_analysis_intake`、`records_milk_analysis_evaluate`、`records_growth_read`、`plans_milk_plan_propose`、`plans_milk_schedule_propose`、`plans_milk_task_update_propose`、`plans_milk_task_delete_propose`、`notifications_milk_reminder_propose`、`records_feeding_record_propose`、`records_feeding_record_delete_propose`、`records_pumping_record_propose`、`records_pumping_record_delete_propose`、`records_growth_record_propose`、`records_growth_record_update_propose`、`records_growth_record_delete_propose`、`ibclc_consult_card_create`。

### 设备服务智能体（5）

`devices_pump_status_read`、`devices_guidance_read`、`devices_unboxing_advance`、`hospital_bag_pump_recommend`、`support_ticket_propose`。

## `propose` 语义

当前 `_propose` 只表示向 Action Runtime 提交结构化变更意图，不能表示是否直接写入；实际行为以 `ActionPolicy.requires_confirmation` 为准。

## Tool 之外的能力

Health Web Search 是主智能体的 Provider 能力。

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
