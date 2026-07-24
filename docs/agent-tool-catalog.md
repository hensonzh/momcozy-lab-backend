# Agent Tool / Action Catalog

本文档是当前 MomCozy Agent 的工具与 Action 边界快照。方案变更时直接覆盖本文档，不在文档内保留旧版本兼容口径。

## 1. 统一契约

`ToolContract` 只声明模型选择工具和 Runtime 执行工具实际需要的信息：

- `name`、`domain`、`description`
- `input_schema`、可选的 `output_schema`
- `effect_scope`
- `timeout_seconds`

`effect_scope` 的含义：

- `effect_scope=none`：只读或纯计算，不产生持久化状态。
- `effect_scope=agent_internal`：只改变 Agent 内部的 Workflow、Artifact 或进度，不作为业务 Action。
- `effect_scope=user_resource`：该工具的至少一个操作可以改变用户业务资源。
- `effect_scope=external_resource`：该工具的至少一个操作可以改变外部系统资源。当前没有此类模型工具。

约束：

- Tool Contract 不声明 `action_type`、`blocking_policy`、`result_dependency`、权限、owner scope 或审计策略。
- Tool Executor 对调用执行同步等待与超时控制，并在 Handler 前后校验已声明的 Input / Output Schema。
- 统一 Tool 可以依据 `operation` 路由到多个内部 Action Type；Tool 的默认 `effect_scope` 也可由执行策略按具体操作收窄。
- 风险等级、是否需要确认、是否允许编辑 payload，只由 `ActionPolicy` 决定，不在 Tool Contract 里重复声明。
- Worker 启动时对 Action Policy 和 Action Handler 做双向完整性校验；缺失任一绑定则拒绝启动。
- 当前用户身份由 Runtime 注入；owner scope、幂等、事务和审计由领域 Service / Repository 执行。
- 模型不存在绕过 Tool 的 `action_proposals` 通道。模型侧 Action 只能由 Action-backed Tool 产生。
- Tool Handler 统一返回 `ToolResult`，作为 `function_call_output` 按 Agent Loop 顺序追加。

## 2. 数量快照

| 项目 | 数量 |
| --- | ---: |
| 模型可见 Tool Contract | 26 |
| 顶层工具 | 26 |
| 默认 `none` | 6 |
| 默认 `agent_internal` | 7 |
| 默认 `user_resource` | 13 |
| Runtime Action Policy / Handler 绑定 | 23 |

## 3. 工具暴露方式

全部 26 个 Tool Contract 都以顶层函数工具直接暴露给对应智能体。每个智能体只接收其静态 Tool Allowlist，不使用 Namespace、deferred loading 或 `tool_search`。

Tool Contract 与 Responses API 函数名使用同一个 canonical `snake_case` 名称；Action Type 只存在于 Runtime 的 Action Policy、Action Handler 和持久化 Action 中。

## 4. 无业务写入工具

### `none`

`maternal_infant_profile_read`、`lactation_timeline_read`、`plans_current_read`、`plans_calendar_read`、`pregnancy_diary_query`、`conversation_history_image_load`。

### `agent_internal`

`milk_analysis`、`devices_guidance`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_pump_recommend`、`support_ticket_propose`、`ibclc_consult_card_create`。

`agent_internal` 仍可以写 Agent Runtime 自身的 Workflow 或 Artifact，但不允许从 Tool Handler 直接修改 Profile、Diary、Plan、Record、Notification 等用户业务资源。

`milk_analysis` 的默认 Contract 为 `agent_internal`，其中 `operation=review` 由执行策略收窄为 `none`。`pregnancy_plan_workflow` 的 Contract 因 `generate_plan` 可写业务资源而声明为 `user_resource`；其余命令由执行策略降为 `agent_internal`。

## 5. Action-backed Tool

| Tool Contract | Runtime Action Type |
| --- | --- |
| `maternal_infant_profile_update` | `profile.update` |
| `pregnancy_diary_save` | `pregnancy_diary.entry.save` |
| `pregnancy_diary_delete` | `pregnancy_diary.entry.delete` |
| `plans_milk_plan_propose` | `plans.milk_plan.create` |
| `lactation_timeline_manage`（日程） | `plans.task.create`、`plans.task.update`、`plans.task.complete`、`plans.task.delete`、`plans.milk_schedule.reschedule` |
| `lactation_timeline_manage`（喂养） | `records.feeding_record.create`、`records.feeding_record.update`、`records.feeding_record.delete` |
| `lactation_timeline_manage`（吸奶） | `records.pumping_record.create`、`records.pumping_record.update`、`records.pumping_record.delete` |
| `lactation_timeline_manage`（生长） | `records.growth_record.create`、`records.growth_record.update`、`records.growth_record.delete` |
| `pregnancy_plan_workflow`（`generate_plan`） | `pregnancy.plan.create` |
| `plans_task_create_propose` | `plans.task.create` |
| `plans_task_complete_propose` | `plans.task.complete` |
| `plans_task_update_propose` | `plans.task.update` |
| `plans_task_delete_propose` | `plans.task.delete` |
| `plans_plan_delete_propose` | `plans.plan.delete` |
| `notifications_milk_reminder_propose` | `notifications.milk_reminder.create` |
| `hospital_bag_cart_update` | `hospital_bag.cart.update` |

## 6. 泌乳服务工具

泌乳服务智能体只暴露 8 个入口：

`maternal_infant_profile_read`、`maternal_infant_profile_update`、`lactation_timeline_read`、`lactation_timeline_manage`、`milk_analysis`、`plans_milk_plan_propose`、`notifications_milk_reminder_propose`、`ibclc_consult_card_create`。

- `lactation_timeline_read` 按日期范围统一读取计划日程与实际喂养、吸奶、生长记录，并通过 `plan_task_id` 合并计划和实际。
- `lactation_timeline_manage` 以 `operation` 与 `item_type` 统一执行记录和日程的新增、更新、删除、状态修改与批量重排；它只统一模型入口，不合并底层 Action。
- `milk_analysis` 以 `review`、`start_or_resume`、`answer`、`evaluate` 统一轻量状态、详细快照、耐久采集和确定性评估。
- `plans_milk_plan_propose` 继续负责完整追奶、稳奶或减奶计划，不塞入通用日程 CRUD。
- Reminder 和 IBCLC 保持独立，因为它们是不同的外显产物与交互边界。

三个统一 Tool 均注册带字段说明的 Output Schema；Executor 在结果进入模型上下文前校验。奶量分析只消费实际记录，不把未执行或仅手动完成的计划任务当作产出或摄入事实。

## 7. Profile 与孕期日记

- `maternal_infant_profile_read` 是统一的妈妈/宝宝基础资料读取入口。默认 `infant_scope=current_delivery`，只返回当前分娩宝宝供奶量分析；`infant_scope=all` 返回全部宝宝，承接通用资料核对和获取稳定 `infant_id` 的需求。
- 读取结果从通用妈妈/宝宝档案、泌乳专用喂养状态和各宝宝最新有效生长记录组装；包含妈妈称呼、年龄、预产期和当前分娩信息，以及宝宝姓名、出生关系、出生日期/性别/体重/孕周、年龄和最新测量。已有妈妈实际分娩日期或当前宝宝实际出生日期时，`estimated_due_date` 在工具输出中固定投影为 `null`，但不删除数据库原始值。
- `maternal_infant_profile_update` 与读取工具字段对应，以 `mother`、`infants` 和 `current_infants` 执行 PATCH；同一个 `profile.update` Action 事务组合通用 Profile Service 与 Lactation Context Service，支持多宝宝关联和出生顺序更新。
- 两个 Tool 都注册嵌套 Output Schema，字段包含含义、单位、枚举、可空或派生说明；Executor 在输出进入模型上下文前校验。缺失字段和数据质量问题使用固定 `{code, birth_order}` 结构。
- 孕期日记拆成 `pregnancy_diary_query` / `pregnancy_diary_save` / `pregnancy_diary_delete`，不再使用一个混合读写工具。
- `save` 只接受 `operation=create|update`；`update` 传完整重写正文。
- `delete` 在 Tool Handler 中校验当前用户消息的 `confirmation_evidence`，证据成立后直接执行 Action，不再生成第二张确认卡。
- `pregnancy_diary.changed` 由 Action Handler / Action Executor 产生，不再由 Tool Handler 伪造领域变更事件。

## 8. 售后提交

`support_ticket_propose` 只生成 `support_ticket_draft` Artifact，不直接创建工单。用户在 Flutter 表单点击“确认并提交”后，`POST /v1/support/tickets` 以 `source=agent_form` 和 `artifact_id` 验证 owner/run/thread 归属，然后同步产生并执行 `support.ticket.create` Action。该点击已是可信用户意图，不再追加二次确认。

普通、非 Agent 表单来源的 Support API 仍是应用命令，不强制伪装成 Agent Action。

## 9. 权威代码位置

- Tool Contract：`app/agents/cozymate/tools/registry.py`
- Input Schema：`app/agents/cozymate/tools/schemas.py`
- Output Schema：`app/agents/cozymate/tools/output_schemas.py`
- Tool Handler：`app/agents/cozymate/tools/handlers/`（按业务能力分组）
- Action Policy：`app/agents/cozymate/actions/policy.py`
- Action Handler 组装：`app/agents/cozymate/actions/registry.py`
- Action Executor：`app/agent_runtime/actions/executor.py`
- 启动期绑定校验：`app/agents/cozymate/factory.py`
