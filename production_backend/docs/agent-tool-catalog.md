# Agent Tool Catalog

本文档是当前 MomCozy Agent 模型可见工具与 namespace 的审查快照，便于评审工具是否必要、命名是否清晰、分组是否合理，以及后续变更是否意外扩大模型工具面。

快照基线：`feat/test1`，2026-07-12，以本文档所在 commit 为准。

## 1. 口径与运行时语义

- 工具注册表只登记**模型可见工具**。当前共 41 个 tool contract。
- 当前生产 OpenAI Responses 路径向模型提供 4 个独立全局工具和 7 个全局 namespace；namespace 内共 37 个工具。
- namespace 不与 service skill 强绑定。模型无需先加载某个 skill 才能看到或检索某个 namespace。
- `recommended_tools` 只是在 `load_service_skill` 返回中的建议清单，不承担权限控制，也不改变工具曝光范围。
- `load_service_skill` 的模型可见工具结果只返回加载状态；完整 skill instructions、`recommended_tools` 和 `business_facts` 仅通过可信 `model_context` 注入一次。持久化 tool output 仍保留安全投影，供审计和排障。
- `eager` 工具随本轮 tools 定义直接提供；namespace 内的 `deferred` 工具通过 Responses API 的 `defer_loading=true` 和 `tool_search` 按需检索。
- canonical contract 名用于 runtime 注册、校验、执行和审计；包含 `.` 的名称传给模型时会转换为 `_`。例如 `profile.read` 对模型显示为 `profile_read`。
- 若 runner 不支持 namespace，runtime 会退化为扁平工具集；本文主要记录当前 OpenAI Responses namespace 路径。

### 数量汇总

| 项目 | 数量 |
| --- | ---: |
| 模型可见 tool contract | 41 |
| 独立全局工具 | 4 |
| 全局 namespace | 7 |
| namespace 内工具 | 37 |
| eager 工具 | 12 |
| deferred 工具 | 29 |

## 2. 独立全局工具

这些工具不属于任何 namespace，始终作为顶层 function tool 提供给模型。

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途与调用条件 |
| --- | --- | --- | --- | --- | --- |
| `load_service_skill` | `load_service_skill` | eager | read | 否 | 用户请求需要进入服务流程且技能尚未驻留时，按 `service_skill_id` 加载技能说明、建议工具和业务事实包。 |
| `profile.read` | `profile_read` | eager | read | 否 | 用户问题或后续动作需要核对姓名、年龄、孕产状态或宝宝资料时，读取当前用户及宝宝的资料投影。 |
| `profile_update` | `profile_update` | eager | write | 否 | 用户明确提供或更正姓名、年龄或 onboarding 状态时更新资料。 |
| `images.inspect` | `images_inspect` | eager | read | 否 | 用户询问当前可见历史中的某张图片时，由模型选择对应 `image_url`，让当前 agent loop 追加图片并进行多模态理解。 |

## 3. 全局 Namespace

| Namespace | 工具数 | eager | deferred | 定位 |
| --- | ---: | ---: | ---: | --- |
| `milk_management` | 17 | 6 | 11 | 用户查看或记录喂养、吸奶、生长数据，分析奶量趋势，或管理相关计划与提醒时使用。 |
| `birth_prep` | 10 | 0 | 10 | 用户制定孕期计划、梳理分娩偏好、生成沟通单或整理待产包时使用。 |
| `hospital_bag_cart` | 1 | 0 | 1 | 用户调整已有待产包购物车的预算、物品、数量或吸奶器时使用。 |
| `pump_recommendation` | 1 | 0 | 1 | 用户购买前询问吸奶器型号、差异、价格或如何选择时使用。 |
| `device_support` | 3 | 2 | 1 | 用户查看设备状态、需要官方指导、排查问题或联系售后时使用。 |
| `health_consultation` | 1 | 0 | 1 | 用户希望联系 IBCLC 哺乳顾问或进一步人工咨询时使用。 |
| `pregnancy_diary` | 1 | 1 | 0 | 用户查看、记录、补充、修改或删除孕期日记时使用。 |

## 4. `milk_management`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `records.milk_status.read` | `records_milk_status_read` | eager | read | 否 | 用户询问当前奶量表现、近期是否有记录或今日状态时读取确定性快照。 |
| `records.milk_summary.read` | `records_milk_summary_read` | eager | read | 否 | 用户回顾近期喂养、吸奶记录或需要奶量概览时读取简要摘要。 |
| `records.milk_analysis.read` | `records_milk_analysis_read` | eager | read | 否 | 用户分析奶量变化、趋势或与宝宝生长的关系时读取分析快照。 |
| `records.growth.read` | `records_growth_read` | eager | read | 否 | 用户查看宝宝近期身高、体重、头围或生长趋势时读取记录。 |
| `records.feeding_record.propose` | `records_feeding_record_propose` | deferred | write | 否 | 用户要求记录喂养时间、方式或奶量时保存一次喂养记录。 |
| `records.feeding_record_delete.propose` | `records_feeding_record_delete_propose` | deferred | write | 否 | 用户明确要求且 owner-scoped 记录唯一确定时同步软删除；目标含糊时先追问。 |
| `records.pumping_record.propose` | `records_pumping_record_propose` | deferred | write | 否 | 用户要求记录吸奶时间、时长、档位或奶量时保存一次吸奶记录。 |
| `records.pumping_record_delete.propose` | `records_pumping_record_delete_propose` | deferred | write | 否 | 用户明确要求且 owner-scoped 记录唯一确定时同步软删除；目标含糊时先追问。 |
| `records.growth_record.propose` | `records_growth_record_propose` | deferred | write | 否 | 用户要求记录宝宝身高、体重或头围时保存一次生长记录。 |
| `records.growth_record_update.propose` | `records_growth_record_update_propose` | deferred | write | 否 | 用户明确更正且 owner-scoped 记录唯一确定时同步修改。 |
| `records.growth_record_delete.propose` | `records_growth_record_delete_propose` | deferred | write | 否 | 用户明确要求且 owner-scoped 记录唯一确定时同步软删除。 |
| `plans.current.read` | `plans_current_read` | eager | read | 否 | 用户查看当前计划、待办或后续安排时读取生效计划和近期任务。 |
| `plans.calendar.read` | `plans_calendar_read` | eager | read | 否 | 用户询问某天安排、待完成事项或任务状态时按日期和状态读取日程。 |
| `plans.milk_plan.propose` | `plans_milk_plan_propose` | deferred | write | 是 | 用户确认奶量计划方向后提交方向和明确约束，由 runtime 生成待确认的可执行计划；覆盖日期已有未来任务时，必须由用户明确选择追加或替换，确认后同事务写入 Plan 与 PlanTask。 |
| `plans.milk_schedule.propose` | `plans_milk_schedule_propose` | deferred | write | 是 | 用户新增生活事项或要求避开已有不可用时段时生成奶量日程重排预览；确认后新增事项与奶量任务调整在同一事务写入。 |
| `plans.task_complete.propose` | `plans_task_complete_propose` | deferred | write | 否 | 用户明确表示唯一指定的单项任务已完成/取消完成时同步更新。 |
| `plans.task_create.propose` | `plans_task_create_propose` | deferred | write | 否 | 用户明确要求新增一项内容和归属清晰的待办时同步创建；批量或含糊范围先澄清。 |
| `notifications.milk_reminder.propose` | `notifications_milk_reminder_propose` | deferred | write | 是 | 用户要求在指定时间收到奶量、喂养或吸奶提醒时创建确认。 |

## 5. `birth_prep`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `pregnancy.plan_intake.start` | `pregnancy_plan_intake_start` | deferred | write | 否 | 用户同意开始制定孕期计划且当前没有 active 计划时，创建一张可信基础信息表单。 |
| `pregnancy.plan_intake.analyze` | `pregnancy_plan_intake_analyze` | deferred | write | 否 | 只消费应用侧已校验的孕期计划表单提交，按风险/信息缺口进入 0..3 轮不重复个性化追问或产检资料步骤。 |
| `pregnancy.plan_intake.advance` | `pregnancy_plan_intake_advance` | deferred | write | 否 | 只推进当前可信步骤：个性化追问、孕早期产检确认、当前 run 附件上传/跳过、最终补充确认。 |
| `pregnancy.plan.propose` | `pregnancy_plan_propose` | deferred | write | 否 | intake 完成产检资料步骤并进入 `ready_to_generate` 后，在当前 agent tool 事务中基于可信快照同步创建计划；失败时返回明确失败且不消费 workflow。 |
| `pregnancy.plan_todo.propose` | `pregnancy_plan_todo_propose` | deferred | write | 否 | 用户明确完成或取消完成当前孕期计划事项，且可信上下文能唯一提供 `plan_id`、`item_id` 与 `version` 时同步更新嵌入卡片的待办状态。 |
| `plans.plan_delete.propose` | `plans_plan_delete_propose` | deferred | write | 否 | 用户当前明确删除且 owner-scoped `plan_id` 唯一确定时立即同步删除，不再追加口头/通用确认；仅目标含糊时澄清。 |
| `plans.task_update.propose` | `plans_task_update_propose` | deferred | write | 否 | 用户明确调整 owner-scoped 唯一单项任务时同步更新；批量修改不走该工具。 |
| `plans.task_delete.propose` | `plans_task_delete_propose` | deferred | write | 否 | 用户明确删除 owner-scoped 唯一单项任务时同步软删除；目标含糊时先澄清。 |
| `birth_plan_form_create` | `birth_plan_form_create` | deferred | write | 否 | 用户开始梳理分娩偏好或准备沟通单时创建信息采集表单。 |
| `labor_communication_card_create` | `labor_communication_card_create` | deferred | write | 否 | 用户完成可信表单并要求生成沟通单时创建可渲染卡片。 |
| `hospital_bag_form_create` | `hospital_bag_form_create` | deferred | write | 否 | 用户确认开始整理待产包时创建信息采集表单。 |
| `hospital_bag_card_create` | `hospital_bag_card_create` | deferred | write | 否 | 用户完成可信表单并要求生成待产包清单时创建可渲染卡片。 |

## 6. `hospital_bag_cart`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `hospital_bag_cart_update` | `hospital_bag_cart_update` | deferred | write | 否 | 用户在已有待产包购物车中调整预算、商品、数量、已有物品或吸奶器时返回前端更新。 |

## 7. `pump_recommendation`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `hospital_bag_pump_recommend` | `hospital_bag_pump_recommend` | deferred | write | 否 | 用户购买前询问型号、差异、预算内选择或价格时，根据偏好从 Momcozy 官方目录推荐。 |

## 8. `device_support`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `devices.pump_status.read` | `devices_pump_status_read` | eager | read | 否 | 用户询问设备连接、在线状态、固件或近期运行状态时读取设备摘要。 |
| `devices.guidance.read` | `devices_guidance_read` | eager | read | 否 | 用户需要安装、使用、清洁或排查设备问题时，按型号、主题或步骤读取官方文档与素材。 |
| `devices.unboxing.advance` | `devices_unboxing_advance` | eager | write | 否 | 用户确认进入分步开箱，或完成当前主步骤时，持久化进度并返回下一步骤资料。 |
| `support.ticket.propose` | `support_ticket_propose` | deferred | write | 是 | 用户明确希望把设备故障或服务问题提交售后时创建工单确认。 |

## 9. `health_consultation`

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `ibclc_consult_card_create` | `ibclc_consult_card_create` | deferred | write | 否 | 用户希望联系专业哺乳顾问或进一步人工咨询时创建 IBCLC 入口卡片。 |

## 10. `pregnancy_diary`

这个 namespace 独立于全部 service skill。统一工具始终 eager 可见，可按用户意图直接调用，不需要先加载健康咨询或其他 skill。读取和写入均使用“宝宝和我”页面同一份 `pregnancy_diary_entries` 数据。

| Canonical contract | 模型看到的 SDK name | 加载 | 读写 | 需确认 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `pregnancy_diary.manage` | `pregnancy_diary_manage` | eager | write* | 否 | 全局统一管理孕期日记的 read/list/write/update/delete；同日写入时由模型结合旧正文完整重写，删除要求 `confirmed=true`。`read/list` 在运行时按读取事件处理。 |

## 11. Skill 与工具的关系

service skill 只通过 `recommended_tools` 向模型提示常用工具，不拥有工具，也不限制跨 namespace 调用。

| Service skill | 当前 recommended tool contract |
| --- | --- |
| `milk-management` | `milk_management` namespace 的全部 17 个工具 |
| `birth-prep` | `pregnancy.plan_intake.start`、`pregnancy.plan_intake.analyze`、`pregnancy.plan_intake.advance`、`pregnancy.plan.propose`、`pregnancy.plan_todo.propose`、`plans.plan_delete.propose`、`plans.task_update.propose`、`plans.task_delete.propose`、4 个分娩沟通/待产包表单与卡片工具、`hospital_bag_cart_update`、`hospital_bag_pump_recommend` |
| `health-consultation` | `records.milk_status.read`、`ibclc_consult_card_create` |
| `emotion-support` | 空；当前没有专属 recommended tool |
| `device-guidance` | `device_support` namespace 的全部 3 个工具 |

这张映射刻意允许跨 namespace 推荐。例如 `health-consultation` 可以推荐位于 `milk_management` 的 `records.milk_status.read`；孕期计划卡片待办则使用 `birth_prep` 自己的 `pregnancy.plan_todo.propose`，不与普通 `PlanTask` 混用 ID。

孕期日记工具不出现在任何 skill 的 `recommended_tools` 中。它由全局 `pregnancy_diary` namespace 独立提供，不参与 skill 加载、驻留或业务事实投影。

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
