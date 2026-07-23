# Agent Tool / Action Catalog

本文档是当前 MomCozy Agent 的工具与 Action 边界快照。方案变更时直接覆盖本文档，不在文档内保留旧版本兼容口径。

## 1. 统一契约

`ToolContract` 只声明工具对资源的影响边界：

- `effect_scope=none`：只读或纯计算，不产生持久化状态。
- `effect_scope=agent_internal`：只改变 Agent 内部的 Workflow、Artifact 或进度，不作为业务 Action。
- `effect_scope=user_resource`：改变用户业务资源，必须绑定 `action_type`。
- `effect_scope=external_resource`：改变外部系统资源，必须绑定 `action_type`。当前没有此类模型工具。

约束：

- `none` / `agent_internal` 禁止声明 `action_type`。
- `user_resource` / `external_resource` 必须声明 `action_type`。
- 风险等级、是否需要确认、是否允许编辑 payload，只由 `ActionPolicy` 决定，不在 Tool Contract 里重复声明。
- Worker 启动时校验每个 Action-backed Tool 同时存在 Policy 和 Handler；缺失任一绑定则拒绝启动。
- 模型不存在绕过 Tool 的 `action_proposals` 通道。模型侧 Action 只能由 Action-backed Tool 产生。
- Tool Handler 统一返回 `ToolResult`，作为 `function_call_output` 按 Agent Loop 顺序追加。

## 2. 数量快照

| 项目 | 数量 |
| --- | ---: |
| 模型可见 Tool Contract | 48 |
| 顶层工具 | 4 |
| Namespace | 7 |
| Namespace 内工具 | 44 |
| eager | 18 |
| deferred | 30 |
| `none` | 12 |
| `agent_internal` | 13 |
| `user_resource` | 23 |
| Action-backed Tool 对应的唯一 Action Type | 21 |

## 3. 顶层工具

| Tool Contract | effect_scope | Action Type |
| --- | --- | --- |
| `load_service_skill` | `none` | — |
| `profile_read` | `none` | — |
| `profile_update` | `user_resource` | `profile.update` |
| `conversation_history_image_load` | `none` | — |

Tool Contract 与 Responses API 函数名使用同一个 canonical `snake_case` 名称；namespace 和 Action Type 分别使用独立字段表达。

## 4. Namespace

| Namespace | Tool 数 | deferred | 用途 |
| --- | ---: | ---: | --- |
| `milk_management` | 22 | 14 | 奶量、喂养、吸奶、生长、计划与提醒 |
| `birth_prep` | 12 | 12 | 孕期计划、分娩沟通、待产包 |
| `hospital_bag_cart` | 1 | 1 | 待产包购物车 |
| `pump_recommendation` | 1 | 1 | 吸奶器推荐 |
| `device_support` | 4 | 1 | 设备状态、官方指导、开箱、售后草稿 |
| `health_consultation` | 1 | 1 | IBCLC 咨询入口 |
| `pregnancy_diary` | 3 | 0 | 孕期日记查询、保存、删除 |

## 5. 无业务写入工具

### `none`

`load_service_skill`、`profile_read`、`lactation_context_read`、`records_milk_summary_read`、`records_milk_status_read`、`records_milk_analysis_read`、`plans_current_read`、`plans_calendar_read`、`pregnancy_diary_query`、`devices_pump_status_read`、`devices_guidance_read`、`conversation_history_image_load`。

### `agent_internal`

`records_milk_analysis_intake`、`records_milk_analysis_evaluate`、`devices_unboxing_advance`、`pregnancy_plan_intake_start`、`pregnancy_plan_intake_analyze`、`pregnancy_plan_intake_advance`、`birth_plan_form_create`、`labor_communication_card_create`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_pump_recommend`、`support_ticket_propose`、`ibclc_consult_card_create`。

`agent_internal` 仍可以写 Agent Runtime 自身的 Workflow 或 Artifact，但不允许从 Tool Handler 直接修改 Profile、Diary、Plan、Record、Notification 等用户业务资源。

## 6. Action-backed Tool

| Tool Contract | Action Type |
| --- | --- |
| `profile_update` | `profile.update` |
| `pregnancy_diary_save` | `pregnancy_diary.entry.save` |
| `pregnancy_diary_delete` | `pregnancy_diary.entry.delete` |
| `plans_milk_plan_propose` | `plans.milk_plan.create` |
| `plans_milk_schedule_propose` | `plans.milk_schedule.reschedule` |
| `pregnancy_plan_propose` | `pregnancy.plan.create` |
| `plans_task_create_propose` | `plans.task.create` |
| `plans_task_complete_propose` | `plans.task.complete` |
| `pregnancy_plan_todo_propose` | `pregnancy.plan_todo.update` |
| `plans_task_update_propose` | `plans.task.update` |
| `plans_task_delete_propose` | `plans.task.delete` |
| `plans_milk_task_update_propose` | `plans.task.update` |
| `plans_milk_task_delete_propose` | `plans.task.delete` |
| `plans_plan_delete_propose` | `plans.plan.delete` |
| `notifications_milk_reminder_propose` | `notifications.milk_reminder.create` |
| `records_feeding_record_propose` | `records.feeding_record.create` |
| `records_pumping_record_propose` | `records.pumping_record.create` |
| `records_feeding_record_delete_propose` | `records.feeding_record.delete` |
| `records_pumping_record_delete_propose` | `records.pumping_record.delete` |
| `records_growth_record_propose` | `records.growth_record.create` |
| `records_growth_record_update_propose` | `records.growth_record.update` |
| `records_growth_record_delete_propose` | `records.growth_record.delete` |
| `hospital_bag_cart_update` | `hospital_bag.cart.update` |

## 7. Profile 与孕期日记

- `profile_read` 一次返回当前用户与全部宝宝的基础资料；宝宝使用稳定的 `infant_id`。
- `lactation_context_read` 从通用妈妈/宝宝档案、泌乳专用状态和每个当前宝宝的最新有效生长记录组装奶量分析上下文；宝宝按 `birth_order` 分组返回，并包含 `sex_at_birth`，产后天数及各宝宝日龄/月龄均在读取时派生。该 Tool 在 Contract 上注册嵌套 Output Schema，所有输出字段均包含含义、单位、枚举、可空或派生说明，Executor 在输出进入模型上下文前校验。缺失字段和数据质量问题使用固定 `{code, birth_order}` 结构。
- `profile_update` 以 `user` 和 `infants` 两个可选对象执行 PATCH，可在同一 Action 中原子更新用户与指定宝宝；宝宝字段包含出生体重与出生孕周，`ProfileUpdateActionHandler` 是该 Tool 的唯一业务写入入口。
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
- Output Schema：`app/agents/cozymate/tools/output_schemas.py`（当前先覆盖 `lactation_context_read`）
- Namespace：`app/agents/cozymate/tools/namespaces.py`
- Tool Handler：`app/agents/cozymate/tools/handlers/`（按业务能力分组）
- Action Policy：`app/agents/cozymate/actions/policy.py`
- Action Handler 组装：`app/agents/cozymate/actions/registry.py`
- Action Executor：`app/agent_runtime/actions/executor.py`
- 启动期绑定校验：`app/agents/cozymate/factory.py`
