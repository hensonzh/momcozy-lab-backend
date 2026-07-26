# Agent Tool / Action Catalog

## 统一契约

模型可见 Tool 使用 canonical `snake_case` 名称：`read` 表示纯读取；`mutate` 表示同一业务资源内包含多种持久化变更；单一变更直接使用 `create`、`update` 或 `delete`；`manage` 仅用于多阶段流程或读写混合能力。资源变更 Tool 通过 `operation` 明确具体动作，不提供旧名称或兼容别名。

`ToolContract` 声明 `name`、`domain`、`description`、Input/Output Schema、`effect_scope`、`action_types`、等待策略、结果依赖和超时；用户业务写入必须经过 Action Policy 与 Action Handler。

## 数量

| 项目 | 数量 |
| --- | ---: |
| 模型可见 Tool | 17 |
| `none` | 6 |
| `agent_internal` | 6 |
| `user_resource` | 5 |
| Tool 声明的唯一 Action Type | 22 |

## Tool

### Profile

`profile_read`、`profile_update`

### Lactation

`milk_analysis_manage`、`ibclc_consult_card_create`

### Plans and diary

`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`

其中 `plan_read` 统一读取已持久化的计划列表或计划详情；`plan_mutate` 统一创建、更新和删除计划，
通过 `plan_type` 选择创建计划的领域构建器。当前创建支持 `milk_management` 和 `pregnancy`；
更新和删除先按当前用户读取目标计划，再以后端持久化的真实类型执行，模型传入的类型只作一致性校验。

`schedule_timeline_read` / `schedule_timeline_mutate` 统一处理泌乳、孕期、产后恢复和通用日程，
并通过 `entry_type=schedule|execution` 区分计划日程与实际记录；完成 feeding/pumping 泌乳任务时必须同时提交
实际时间和对应奶量，由实际记录 Action 原子完成关联任务。计划本身与日程任务保持独立资源边界。
不再提供领域专用日历读取 Tool。

`diary_read` / `diary_mutate` 统一处理日记读取、创建、完整更新和删除。
资源按“当前用户 + 日期”唯一；孕期、产后恢复和育儿只作为日记内容语义，不作为工具参数或资源分区。
不再提供孕期专用的模型 Tool。

### Prenatal

`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

### Devices and support

`devices_guidance_manage`、`pump_models_read`、`support_ticket_create`

### Conversation assets

`conversation_history_image_read`

## Action 边界

- `profile_update`：`profile.update`
- `schedule_timeline_mutate`：跨领域任务创建、完成、更新和删除，奶量日程冲突感知重排，以及喂养、吸奶和宝宝生长实际记录 Action
- `diary_mutate`：通用日记创建、完整更新与删除 Action
- `plan_mutate`：奶量计划创建、孕期计划创建、计划元数据更新和计划删除 Action
- `hospital_bag_cart_mutate`：待产包购物车更新 Action

`pregnancy_intake_manage` 只维护孕期资料采集工作流；达到 `ready_to_generate` 后由 `plan_mutate`
创建孕期计划。其余 Tool 为只读或仅写 Agent 内部 Workflow / Artifact。
