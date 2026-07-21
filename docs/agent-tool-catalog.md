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
| `profile.read` | `none` | — |
| `profile.update` | `user_resource` | `profile.update` |
| `conversation_history.image.load` | `none` | — |

Canonical contract 中的 `.` 传给 Responses API 时会转换为 `_`，例如 `profile.update` 对模型显示为 `profile_update`；Runtime 内部、审计和 Eval 始终使用 canonical contract。

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

`load_service_skill`、`profile.read`、`records.milk_summary.read`、`records.milk_status.read`、`records.milk_analysis.read`、`records.growth.read`、`plans.current.read`、`plans.calendar.read`、`pregnancy_diary.query`、`devices.pump_status.read`、`devices.guidance.read`、`conversation_history.image.load`。

### `agent_internal`

`records.milk_analysis.intake`、`records.milk_analysis.evaluate`、`devices.unboxing.advance`、`pregnancy.plan_intake.start`、`pregnancy.plan_intake.analyze`、`pregnancy.plan_intake.advance`、`birth_plan_form_create`、`labor_communication_card_create`、`hospital_bag_form_create`、`hospital_bag_card_create`、`hospital_bag_pump_recommend`、`support.ticket.propose`、`ibclc_consult_card_create`。

`agent_internal` 仍可以写 Agent Runtime 自身的 Workflow 或 Artifact，但不允许从 Tool Handler 直接修改 Profile、Diary、Plan、Record、Notification 等用户业务资源。

## 6. Action-backed Tool

| Tool Contract | Action Type |
| --- | --- |
| `profile.update` | `profile.update` |
| `pregnancy_diary.save` | `pregnancy_diary.entry.save` |
| `pregnancy_diary.delete` | `pregnancy_diary.entry.delete` |
| `plans.milk_plan.propose` | `plans.milk_plan.create` |
| `plans.milk_schedule.propose` | `plans.milk_schedule.reschedule` |
| `pregnancy.plan.propose` | `pregnancy.plan.create` |
| `plans.task_create.propose` | `plans.task.create` |
| `plans.task_complete.propose` | `plans.task.complete` |
| `pregnancy.plan_todo.propose` | `pregnancy.plan_todo.update` |
| `plans.task_update.propose` | `plans.task.update` |
| `plans.task_delete.propose` | `plans.task.delete` |
| `plans.milk_task_update.propose` | `plans.task.update` |
| `plans.milk_task_delete.propose` | `plans.task.delete` |
| `plans.plan_delete.propose` | `plans.plan.delete` |
| `notifications.milk_reminder.propose` | `notifications.milk_reminder.create` |
| `records.feeding_record.propose` | `records.feeding_record.create` |
| `records.pumping_record.propose` | `records.pumping_record.create` |
| `records.feeding_record_delete.propose` | `records.feeding_record.delete` |
| `records.pumping_record_delete.propose` | `records.pumping_record.delete` |
| `records.growth_record.propose` | `records.growth_record.create` |
| `records.growth_record_update.propose` | `records.growth_record.update` |
| `records.growth_record_delete.propose` | `records.growth_record.delete` |
| `hospital_bag_cart_update` | `hospital_bag.cart.update` |

## 7. Profile 与孕期日记

- `profile.update` 只做参数归一化和 Action 提交；`ProfileUpdateActionHandler` 是唯一业务写入入口。
- 孕期日记拆成 `pregnancy_diary.query` / `pregnancy_diary.save` / `pregnancy_diary.delete`，不再使用一个混合读写工具。
- `save` 只接受 `operation=create|update`；`update` 传完整重写正文。
- `delete` 在 Tool Handler 中校验当前用户消息的 `confirmation_evidence`，证据成立后直接执行 Action，不再生成第二张确认卡。
- `pregnancy_diary.changed` 由 Action Handler / Action Executor 产生，不再由 Tool Handler 伪造领域变更事件。

## 8. 售后提交

`support.ticket.propose` 只生成 `support_ticket_draft` Artifact，不直接创建工单。用户在 Flutter 表单点击“确认并提交”后，`POST /v1/support/tickets` 以 `source=agent_form` 和 `artifact_id` 验证 owner/run/thread 归属，然后同步产生并执行 `support.ticket.create` Action。该点击已是可信用户意图，不再追加二次确认。

普通、非 Agent 表单来源的 Support API 仍是应用命令，不强制伪装成 Agent Action。

## 9. 权威代码位置

- Tool Contract：`app/agents/cozymate/tools/registry.py`
- Input Schema：`app/agents/cozymate/tools/schemas.py`
- Namespace：`app/agents/cozymate/tools/namespaces.py`
- Tool Handler：`app/agents/cozymate/tools/handlers/`（按业务能力分组）
- Action Policy：`app/agents/cozymate/actions/policy.py`
- Action Handler 组装：`app/agents/cozymate/actions/registry.py`
- Action Executor：`app/agent_runtime/actions/executor.py`
- 启动期绑定校验：`app/agents/cozymate/factory.py`
