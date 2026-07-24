# Agent Tool / Action Catalog

## 统一契约

模型可见 Tool 使用 canonical `snake_case` 名称；`read` 只读、`write` 写业务资源、`manage` 管理多阶段或混合操作。写工具通过 `operation` 区分 create、update、delete 等操作，不提供旧名称或兼容别名。

`ToolContract` 声明 `name`、`domain`、`description`、Input/Output Schema、`effect_scope`、`action_types`、等待策略、结果依赖和超时；用户业务写入必须经过 Action Policy 与 Action Handler。

## 数量

| 项目 | 数量 |
| --- | ---: |
| 模型可见 Tool | 21 |
| `none` | 7 |
| `agent_internal` | 5 |
| `user_resource` | 9 |
| Tool 声明的唯一 Action Type | 22 |

## Tool

### Profile

`profile_read`、`profile_write`

### Lactation

`lactation_timeline_read`、`lactation_timeline_write`、`milk_analysis_manage`、`plans_milk_plan_write`、`notifications_milk_reminder_write`、`ibclc_consult_card_write`

### Plans and diary

`plans_current_read`、`plans_calendar_read`、`plans_task_write`、`plans_plan_write`、`pregnancy_diary_read`、`pregnancy_diary_write`

### Prenatal

`pregnancy_plan_manage`、`hospital_bag_manage`、`hospital_bag_cart_write`

### Devices and support

`devices_guidance_manage`、`pump_models_read`、`support_ticket_write`

### Conversation assets

`conversation_history_image_read`

## Action 边界

- `profile_write`：`profile.update`
- `lactation_timeline_write`：计划任务、奶量日程重排、喂养、吸奶和生长记录 Action
- `pregnancy_diary_write`：日记保存与删除 Action
- `plans_milk_plan_write`：奶量计划创建 Action
- `pregnancy_plan_manage`：孕期计划创建 Action
- `plans_task_write`：任务创建、完成、更新和删除 Action
- `plans_plan_write`：计划删除 Action
- `notifications_milk_reminder_write`：奶量提醒创建 Action
- `hospital_bag_cart_write`：待产包购物车更新 Action

其余 Tool 为只读或仅写 Agent 内部 Workflow / Artifact。
