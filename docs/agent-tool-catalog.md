# Agent Tool / Action Catalog

## 统一契约

模型可见 Tool 使用 canonical `snake_case` 名称：`read` 表示纯读取；`mutate` 表示同一业务资源内包含多种持久化变更；单一变更直接使用 `create`、`update` 或 `delete`；`manage` 仅用于多阶段流程或读写混合能力。只有包含多种操作的资源变更 Tool 才通过 `operation` 明确动作；单一操作 Tool 不重复传同义的 `operation`，也不提供旧名称或兼容别名。

`ToolContract` 声明 `name`、`domain`、`description`、Input/Output Schema、`effect_scope`、`action_types`、等待策略、结果依赖和超时；用户业务写入必须经过 Action Policy 与 Action Handler。

## 数量

| 项目 | 数量 |
| --- | ---: |
| 模型可见 Tool | 17 |
| `none` | 6 |
| `agent_internal` | 6 |
| `user_resource` | 5 |
| Tool 声明的唯一 Action Type | 23 |

17 个 Tool 只表示全局已注册契约总数，不代表任一模型调用都能看到 17 个 Tool。生产运行时先做无 Tool 的语义路由，
再按以下静态 Allowlist 创建对应模型请求；非法路由不会回退到全量目录。

| 智能体 | 模型可见 Tool 数 | Tool |
| --- | ---: | --- |
| 主智能体 | 9 | `profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`、`conversation_history_image_read` |
| 产前服务 | 5 | `plan_read`、`plan_mutate`、`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate` |
| 泌乳服务 | 8 | `profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`milk_analysis_manage`、`ibclc_consult_card_create` |
| 设备服务 | 3 | `devices_guidance_manage`、`pump_models_read`、`support_ticket_create` |

多场景请求中的每次专业模型调用仍分别应用自己的 Allowlist；最终汇总调用不提供任何 Tool。

## Tool

### Profile

`profile_read`、`profile_update`

`profile_read` 默认只读取通过 `maternal_current_delivery_infants` 明确关联的本次分娩宝宝；不再根据“只有一个宝宝档案”
临时猜测当前宝宝。`infant_scope=all` 用于通用资料核对和取得稳定 `infant_id`。超过 10 个宝宝时，最近生长测量在
服务端分批查询，模型仍获得一个规范化结果。

`profile_update` 的模型输入只包含 `mother`、`infants`、`current_infants` 三类业务字段，不暴露冗余
`operation`、内部 owner 字段或幂等键。普通字段更新通过 `profile.update` 同步应用；完整替换
`current_infants`（包括清空）使用 `profile.current_infants.replace`，保存提案时的关系快照并要求用户确认，
执行前发生变化则返回版本冲突。成功写入后重新读取并返回规范化 Profile，而不是只返回提交字段摘要。

预产期与产后事实在持久化层保持互斥：一旦存在实际分娩日期或明确关联的当前宝宝出生日期，
`user_profiles.estimated_due_date` 会被清空；并发资料更新按用户串行。当前分娩方式为 `cesarean` 时，
`has_cesarean_history` 必须为 `true`。日期上限校验使用 Runtime 注入的用户本地日期。

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

所有模型可见 Tool schema 只承载业务意图，不暴露 `idempotency_key`。计划、日程、实际记录、日记、
母婴资料和待产包购物车等 Action 的幂等键均由 Runtime 根据当前 run 与规范化写入 payload 稳定生成；
App/API 层的网络请求幂等仍由请求头或应用请求参数负责。

### Prenatal

`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

### Devices and support

`devices_guidance_manage`、`pump_models_read`、`support_ticket_create`

### Conversation assets

`conversation_history_image_read`

## Action 边界

- `profile_update`：普通资料字段使用 `profile.update`；完整替换当前分娩宝宝关系使用
  `profile.current_infants.replace`，后者必须确认且带关系新鲜度前置条件
- `schedule_timeline_mutate`：跨领域任务创建、完成、更新和删除，奶量日程冲突感知重排，以及喂养、吸奶和宝宝生长实际记录 Action
- `diary_mutate`：通用日记创建、完整更新与删除 Action
- `plan_mutate`：奶量计划创建、孕期计划创建、计划元数据更新和计划删除 Action
- `hospital_bag_cart_mutate`：待产包购物车更新 Action

`pregnancy_intake_manage` 只维护孕期资料采集工作流；达到 `ready_to_generate` 后由 `plan_mutate`
创建孕期计划。其余 Tool 为只读或仅写 Agent 内部 Workflow / Artifact。
