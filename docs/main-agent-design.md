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
- `read` 表示纯读取；`mutate` 表示同一业务资源内包含多种持久化变更；单一变更直接使用 `create`、`update` 或 `delete`；`manage` 仅表示同一能力内的多阶段流程或读写混合操作。
- 同一资源存在多种变更时合并为一个 `mutate` Tool，并通过 `operation` 区分；只有单一变更时，工具名直接表达具体动作。
- 每个智能体仅接收自己的静态 Tool Allowlist；Tool 调用和结果按 Agent Loop 顺序进入上下文。
- 跨领域计划摘要、日程和已关联实际事实统一从 `schedule_timeline_read` 读取；不再为泌乳、孕期或产后康复分别提供日历读取 Tool。
- 计划日程和实际记录统一由 `schedule_timeline_mutate` 管理，并通过 `entry_type=schedule|execution` 区分；完成 feeding/pumping 泌乳任务必须同时写入实际时间和对应奶量，由实际记录 Action 原子完成关联任务。
- 已持久化的计划统一由 `plan_read` 读取，由 `plan_mutate` 创建、更新和删除；创建时使用 `plan_type` 选择领域构建器，更新和删除以后端读取到的真实计划类型为准。
- `plan_read` 不代替日程时间线读取；计划下的任务、完成状态和实际执行事实仍由 `schedule_timeline_read` / `schedule_timeline_mutate` 管理。
- 日记统一由 `diary_read` / `diary_mutate` 读取和变更；同一用户、同一日期最多一条。孕期、产后恢复和育儿只是
  日记内容语义，不作为资源类型或存储分区；不再提供孕期专用的模型 Tool。
- `pregnancy_intake_manage` 只负责孕期资料采集；采集完成后通过 `plan_mutate` 创建孕期计划，不在一个工具内混合采集与业务资源写入。

## Tool 归属（17）

### 主智能体与公共能力（9）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`、`conversation_history_image_read`

### 产前服务智能体（5）

`plan_read`、`plan_mutate`、`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

### 泌乳服务智能体（8）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`milk_analysis_manage`、`ibclc_consult_card_create`

### 设备服务智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_create`

## 实施约束

当前仅在测试环境供少量内部用户使用，数据可直接清理；方案直接切换，不兼容旧架构、旧 Tool 名称、旧契约或旧测试数据。
