# Agent Tool Catalog

本文档是当前 MomCozy Agent 模型可见工具与 namespace 的审查快照，便于评审工具是否必要、命名是否清晰、分组是否合理，以及后续变更是否意外扩大模型工具面。

快照基线：`feat/test1`，2026-07-11，以本文档所在 commit 为准。

## 1. 口径与运行时语义

- 工具注册表只登记**模型可见工具**。当前共 39 个 tool contract。
- 当前生产 OpenAI Responses 路径向模型提供 4 个独立全局工具和 7 个全局 namespace；namespace 内共 35 个工具。
- namespace 不与 service skill 强绑定。模型无需先加载某个 skill 才能看到或检索某个 namespace。
- `recommended_tools` 只是在 `load_service_skill` 返回中的建议清单，不承担权限控制，也不改变工具曝光范围。
- `eager` 工具随本轮 tools 定义直接提供；namespace 内的 `deferred` 工具通过 Responses API 的 `defer_loading=true` 和 `tool_search` 按需检索。
- canonical contract 名用于 runtime 注册、校验、执行和审计；包含 `.` 的名称传给模型时会转换为 `_`。例如 `profile.read` 对模型显示为 `profile_read`。
- 若 runner 不支持 namespace，runtime 会退化为扁平工具集；本文主要记录当前 OpenAI Responses namespace 路径。

### 数量汇总

| 项目 | 数量 |
| --- | ---: |
| 模型可见 tool contract | 39 |
| 独立全局工具 | 4 |
| 全局 namespace | 7 |
| namespace 内工具 | 35 |
| eager 工具 | 12 |
| deferred 工具 | 27 |

## 2. 独立全局工具

这些工具不属于任何 namespace，始终作为顶层 function tool 提供给模型。

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途与调用条件 |
| --- | --- | --- | --- | --- | --- |
| `load_service_skill` | `load_service_skill` | eager | read | 否 | 进入奶量、产前准备、健康咨询、情绪支持或设备指导流程前，按 `service_skill_id` 加载技能说明、建议工具和小型业务事实包。 |
| `profile.read` | `profile_read` | eager | read | 否 | 读取当前用户的资料上下文投影。 |
| `profile_update` | `profile_update` | eager | write | 否 | 更新用户明确提供的基础资料字段，例如 `display_name`、`age` 或 `onboarding_skipped`。 |
| `images.inspect` | `images_inspect` | eager | read | 否 | 用户询问当前可见历史中的某张图片时，由模型选择对应 `image_url`，让当前 agent loop 追加图片并进行多模态理解。 |

## 3. 全局 Namespace

| Namespace | 工具数 | eager | deferred | 定位 |
| --- | ---: | ---: | ---: | --- |
| `milk_management` | 17 | 6 | 11 | 奶量、喂养、吸奶、生长记录、奶量计划、提醒和奶量任务。 |
| `birth_prep` | 8 | 0 | 8 | 孕期计划、分娩沟通、待产包表单与待产包卡片。 |
| `hospital_bag_cart` | 1 | 0 | 1 | 待产包购物车调整。 |
| `pump_recommendation` | 1 | 0 | 1 | 待产包相关吸奶器推荐。 |
| `device_support` | 3 | 2 | 1 | 吸奶器设备状态、官方指导素材和售后工单。 |
| `health_consultation` | 1 | 0 | 1 | 健康咨询中的 IBCLC 咨询卡片。 |
| `pregnancy_diary` | 4 | 0 | 4 | 独立管理孕期日记的读取、创建、更新和删除。 |

## 4. `milk_management`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `records.milk_status.read` | `records_milk_status_read` | eager | read | 否 | 读取确定性的近期奶量状态快照。 |
| `records.milk_summary.read` | `records_milk_summary_read` | eager | read | 否 | 读取近期喂养、吸奶和趋势的有限摘要。 |
| `records.milk_analysis.read` | `records_milk_analysis_read` | eager | read | 否 | 读取近期奶量、生长和趋势事实并形成分析快照。 |
| `records.growth.read` | `records_growth_read` | eager | read | 否 | 读取当前用户范围内的宝宝身高、体重、头围等记录。 |
| `records.feeding_record.propose` | `records_feeding_record_propose` | deferred | write | 否 | 提出喂养记录创建动作。 |
| `records.feeding_record_delete.propose` | `records_feeding_record_delete_propose` | deferred | write | 是 | 提出删除喂养记录动作。 |
| `records.pumping_record.propose` | `records_pumping_record_propose` | deferred | write | 否 | 提出吸奶记录创建动作。 |
| `records.pumping_record_delete.propose` | `records_pumping_record_delete_propose` | deferred | write | 是 | 提出删除吸奶记录动作。 |
| `records.growth_record.propose` | `records_growth_record_propose` | deferred | write | 否 | 提出宝宝生长记录创建动作。 |
| `records.growth_record_update.propose` | `records_growth_record_update_propose` | deferred | write | 是 | 提出宝宝生长记录更新动作。 |
| `records.growth_record_delete.propose` | `records_growth_record_delete_propose` | deferred | write | 是 | 提出宝宝生长记录删除动作。 |
| `plans.current.read` | `plans_current_read` | eager | read | 否 | 读取当前生效计划和近期任务的有限摘要。 |
| `plans.calendar.read` | `plans_calendar_read` | eager | read | 否 | 按日期、状态读取当前用户的计划任务日程。 |
| `plans.milk_plan.propose` | `plans_milk_plan_propose` | deferred | write | 是 | 创建奶量计划预览并提出计划创建动作。 |
| `plans.task_complete.propose` | `plans_task_complete_propose` | deferred | write | 是 | 提出计划任务完成状态更新动作。 |
| `plans.task_create.propose` | `plans_task_create_propose` | deferred | write | 是 | 提出计划任务创建动作。 |
| `notifications.milk_reminder.propose` | `notifications_milk_reminder_propose` | deferred | write | 是 | 提出奶量管理提醒通知动作。 |

## 5. `birth_prep`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `pregnancy.plan.propose` | `pregnancy_plan_propose` | deferred | write | 是 | 基于 runtime 的可信孕期资料和本轮偏好，创建孕期计划预览并提出创建动作。 |
| `plans.plan_delete.propose` | `plans_plan_delete_propose` | deferred | write | 是 | 提出计划删除动作。 |
| `plans.task_update.propose` | `plans_task_update_propose` | deferred | write | 是 | 提出计划任务日期、时间、标题、描述或载荷更新动作。 |
| `plans.task_delete.propose` | `plans_task_delete_propose` | deferred | write | 是 | 提出计划任务删除动作。 |
| `birth_plan_form_create` | `birth_plan_form_create` | deferred | write | 否 | 创建分娩沟通单信息采集表单；模型只决定是否创建，字段和预填由 runtime/工具生成。 |
| `labor_communication_card_create` | `labor_communication_card_create` | deferred | write | 否 | 根据应用侧注入的 `birth_plan_card_intake` 表单数据生成前端可渲染的分娩沟通单。 |
| `hospital_bag_form_create` | `hospital_bag_form_create` | deferred | write | 否 | 创建待产包信息采集表单；runtime 合并可信 profile 和 active 孕期计划信息。 |
| `hospital_bag_card_create` | `hospital_bag_card_create` | deferred | write | 否 | 根据应用侧注入的 `hospital_bag_intake` 表单数据生成前端可渲染的待产包清单。 |

## 6. `hospital_bag_cart`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `hospital_bag_cart_update` | `hospital_bag_cart_update` | deferred | write | 否 | 在已有待产包购物车或明确处于购物车调整流程时，根据自然语言调整预算、商品、数量或吸奶器型号；只返回前端可应用的更新，不真实下单。 |

## 7. `pump_recommendation`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `hospital_bag_pump_recommend` | `hospital_bag_pump_recommend` | deferred | write | 否 | 根据预算、使用场景和偏好，从 Momcozy 官方目录推荐 1 款主推和 1-2 款备选；无购物车副作用。 |

## 8. `device_support`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `devices.pump_status.read` | `devices_pump_status_read` | eager | read | 否 | 读取当前用户吸奶器设备和近期遥测状态的有限摘要。 |
| `devices.guidance_assets.read` | `devices_guidance_assets_read` | eager | read | 否 | 按元数据读取已打包设备指导素材的有限列表。 |
| `support.ticket.propose` | `support_ticket_propose` | deferred | write | 是 | 在用户确认售后意图明确后，创建售后工单动作提案。 |

## 9. `health_consultation`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `ibclc_consult_card_create` | `ibclc_consult_card_create` | deferred | write | 否 | 创建 IBCLC 哺乳顾问咨询入口卡片，无外部预约副作用。 |

## 10. `pregnancy_diary`

这个 namespace 独立于全部 service skill。模型可按用户意图直接检索和调用，不需要先加载健康咨询或其他 skill。读取和写入均使用“宝宝和我”页面同一份 `pregnancy_diary_entries` 数据。

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `pregnancy_diary.entries.read` | `pregnancy_diary_entries_read` | deferred | read | 否 | 按指定日期或日期范围读取当前用户的孕期日记；回顾日记或更新前获取原记录时调用。 |
| `pregnancy_diary.entry_create.propose` | `pregnancy_diary_entry_create_propose` | deferred | write | 否 | 用户明确要求记录且当天尚无日记时，创建正文及可选心情、睡眠、胎动、症状和产检问题。 |
| `pregnancy_diary.entry_update.propose` | `pregnancy_diary_entry_update_propose` | deferred | write | 否 | 用户明确补充或修改已有日记时更新；正文更新前需读取原记录并提交合并后的完整正文。 |
| `pregnancy_diary.entry_delete.propose` | `pregnancy_diary_entry_delete_propose` | deferred | write | 是 | 用户明确要求删除某天日记时提出删除动作并等待确认。 |

## 11. Skill 与工具的关系

service skill 只通过 `recommended_tools` 向模型提示常用工具，不拥有工具，也不限制跨 namespace 调用。

| Service skill | 当前 recommended tool contract |
| --- | --- |
| `milk-management` | `milk_management` namespace 的全部 17 个工具 |
| `birth-prep` | `pregnancy.plan.propose`、`plans.plan_delete.propose`、`plans.task_complete.propose`、`plans.task_update.propose`、`plans.task_delete.propose`、4 个表单/卡片工具、`hospital_bag_cart_update`、`hospital_bag_pump_recommend` |
| `health-consultation` | `records.milk_status.read`、`ibclc_consult_card_create` |
| `emotion-support` | 空；当前没有专属 recommended tool |
| `device-guidance` | `device_support` namespace 的全部 3 个工具 |

这张映射刻意允许跨 namespace 推荐。例如 `birth-prep` 可以推荐位于 `milk_management` 的 `plans.task_complete.propose`，`health-consultation` 可以推荐位于 `milk_management` 的 `records.milk_status.read`。

孕期日记工具不出现在任何 skill 的 `recommended_tools` 中。它们由全局 `pregnancy_diary` namespace 独立提供，不参与 skill 加载、驻留或业务事实投影。

## 12. 不暴露给模型的内部 Handler

以下名称存在于默认 handler map 或内部执行语义中，但不在 `ToolContractRegistry`，不会进入 Responses API 的 `tools`：

| 内部名称 | 用途 |
| --- | --- |
| `business.context.read` | runtime 内部读取业务上下文。 |
| `pregnancy.plan_context.read` | runtime 内部读取孕期计划上下文。 |

它们不应被加入 namespace，也不应写入 skill 的 `recommended_tools`。若未来确实需要模型调用，应先正式定义 tool contract、schema、description、handler 与测试，再进入注册表。

## 13. 权威来源与审查清单

权威来源：

- Tool contract、description、loading mode：`app/modules/agent_runtime/agents/cozymate_service_agent/tools/registry.py`
- Namespace 及成员关系：`app/modules/agent_runtime/agents/cozymate_service_agent/tools/namespaces.py`
- Input schema：`app/modules/agent_runtime/agents/cozymate_service_agent/tools/schemas.py`
- Contract 到 SDK name 的转换、Responses payload：`app/modules/agent_runtime/sdk/runner.py`
- 每轮工具目录、skill 推荐映射：`app/modules/agent_runtime/run_lifecycle/executor.py`
- 实际 handler：`app/modules/agent_runtime/agents/cozymate_service_agent/tools/handlers.py`

每次新增、删除、改名或移动工具后，至少检查：

1. 注册表只包含模型可见工具，且每个 contract 有有效 description 和 input schema。
2. canonical contract、SDK name、handler key 与测试预期一致。
3. 每个 namespace 成员都已注册，同一 contract 不属于多个 namespace。
4. `eager`/`deferred` 符合调用频率和上下文成本，deferred 工具仍能通过 `tool_search` 找到。
5. skill 的 `recommended_tools` 只引用已注册 contract，并保持“建议而非权限”的语义。
6. 写工具的确认、幂等、审计和副作用声明与 handler 的真实行为一致。
7. 更新本文档中的快照 commit、数量汇总和工具明细。
