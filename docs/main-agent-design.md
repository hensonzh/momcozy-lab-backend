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
| 产前服务智能体 | 孕期计划、孕期计划任务、待产包、待产包购物车、分娩沟通单和事项型孕期焦虑 |
| 泌乳服务智能体 | 奶量摘要与分析、泌乳计划、日程调整、喂养/吸奶/生长记录、吸奶小结、提醒和 IBCLC 衔接 |
| 设备服务智能体 | 吸奶器选型、设备状态、开箱使用、清洁消毒、蓝牙与法兰指导、故障排查和售后工单 |
| 全局能力 | 健康红旗、情绪危机、设备安全、权限、Prompt 防护、上下文账本和 Memory |

## Tool 归属

Tool 只有一个领域归属，但公共 Tool 和有界专业能力可以按 Allowlist 暴露给多个智能体。

### 主智能体与公共能力（13）

`profile.read`、`profile.update`、`plans.current.read`、`plans.calendar.read`、`plans.task_create.propose`、`plans.task_complete.propose`、`plans.task_update.propose`、`plans.task_delete.propose`、`plans.plan_delete.propose`、`pregnancy_diary.query`、`pregnancy_diary.save`、`pregnancy_diary.delete`、`conversation_history.image.load`。

### 产前服务智能体（10）

`pregnancy.plan_intake.start`、`pregnancy.plan_intake.analyze`、`pregnancy.plan_intake.advance`、`pregnancy.plan.propose`、`pregnancy.plan_todo.propose`、`birth_plan_form_create`、`labor_communication_card_create`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_cart_update`。

### 泌乳服务智能体（19）

`records.milk_summary.read`、`records.milk_status.read`、`records.milk_analysis.read`、`records.milk_analysis.intake`、`records.milk_analysis.evaluate`、`records.growth.read`、`plans.milk_plan.propose`、`plans.milk_schedule.propose`、`plans.milk_task_update.propose`、`plans.milk_task_delete.propose`、`notifications.milk_reminder.propose`、`records.feeding_record.propose`、`records.feeding_record_delete.propose`、`records.pumping_record.propose`、`records.pumping_record_delete.propose`、`records.growth_record.propose`、`records.growth_record_update.propose`、`records.growth_record_delete.propose`、`ibclc_consult_card_create`。

### 设备服务智能体（5）

`devices.pump_status.read`、`devices.guidance.read`、`devices.unboxing.advance`、`hospital_bag_pump_recommend`、`support.ticket.propose`。

### 移除（1）

`load_service_skill` 由三个 Handoff 和各智能体静态专业定义取代。

## 调整项

`hospital_bag_pump_recommend` 迁入设备领域并改为设备语义名称，通用计划 Tool 从现有专业 Namespace 移入公共能力面。

## Tool 之外的能力

`health-consultation` 和 `emotion-support` 当前是 Skill，`business.context.read` 与 `pregnancy.plan_context.read` 是内部 Handler，Health Web Search 是 Provider 能力。

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
