# Momcozy 多智能体架构

## 架构

系统由一个主智能体和产前、泌乳、设备三个专业子智能体组成，共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 回复规则

- 通用问题：主智能体直接回复。
- 单一专业意图：主智能体路由，子智能体直接回复。
- 多专业意图：主智能体并行或按依赖顺序调用子智能体，再汇总回复。

主智能体负责通用回答、专业路由和多专业汇总，不持有子智能体的完整专业指令与底层 Tool。

## Tool 规则

- 模型可见 Tool 使用唯一的 canonical `snake_case` 名称，不保留旧名称或兼容别名。
- `read` 表示只读，`write` 表示业务资源写入，`manage` 表示同一能力内的多阶段或混合操作。
- 同一资源的 create、update、delete 合并到一个 `write` Tool，通过 `operation` 区分。
- 每个智能体仅接收自己的静态 Tool Allowlist；Tool 调用和结果按 Agent Loop 顺序进入上下文。

## Tool 归属（21）

### 主智能体与公共能力（9）

`profile_read`、`profile_write`、`plans_current_read`、`plans_calendar_read`、`plans_task_write`、`plans_plan_write`、`pregnancy_diary_read`、`pregnancy_diary_write`、`conversation_history_image_read`

### 产前服务智能体（3）

`pregnancy_plan_manage`、`hospital_bag_manage`、`hospital_bag_cart_write`

### 泌乳服务智能体（8）

`profile_read`、`profile_write`、`lactation_timeline_read`、`lactation_timeline_write`、`milk_analysis_manage`、`plans_milk_plan_write`、`notifications_milk_reminder_write`、`ibclc_consult_card_write`

### 设备服务智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_write`

## 实施约束

当前仅在测试环境供少量内部用户使用，数据可直接清理；方案直接切换，不兼容旧架构、旧 Tool 名称、旧契约或旧测试数据。
