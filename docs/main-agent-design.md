# Momcozy 多智能体架构

## 总体架构

系统采用一个主智能体和产前、泌乳、设备三个专业子智能体，所有智能体共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 实施约束

当前方案仅发布在测试环境，尚无需要保留的正式线上用户数据；少量内部测试用户及其数据后续可直接清理。因此方案和实现默认直接切换，不兼容旧架构、旧契约或既有测试数据，不保留双写、兼容分支和历史数据迁移逻辑。

## 回复与路由

主智能体负责通用回答、专业路由和多专业结果汇总，单一专业任务由对应子智能体直接回复。

主智能体只持有公共 Tool、有界专业能力 Tool 和三个 Handoff，不持有子智能体的完整专业指令与底层 Tool。

## 专业能力装配

- 产前、泌乳和设备三个子智能体在创建时静态绑定各自的完整专业指令与领域 Tool Allowlist。
- 各子智能体 Allowlist 中的 Tool 均作为顶层函数工具直接暴露，不使用 Namespace、deferred loading 或 `tool_search`。
- 主智能体只根据 Handoff 的名称、描述和输入契约进行路由，不通过模型可见 Tool 动态获取专业指令。
- 业务事实通过 owner-scoped Read Tool 或内部 Context Projector 按需读取，不与专业指令一起装载。
- 上下文账本记录 Handoff、Tool 调用和结果，不维护“已加载专业 Skill”状态。
- `service_skill_id` 仅用于 run 归属、评测和观测，不代表可调用的动态加载能力。

## 场景归属

| 归属 | 场景 |
| --- | --- |
| 主智能体 | 通用问答、健康咨询、普通情绪支持、孕期日记、用户资料、通用计划任务、产后恢复问答、范围澄清和多意图汇总 |
| 产前服务智能体 | 孕期计划、孕期计划任务、待产包、待产包购物车和事项型孕期焦虑 |
| 泌乳服务智能体 | 奶量状态与分析、泌乳计划、统一日程与喂养/吸奶/生长记录管理、提醒和 IBCLC 衔接 |
| 设备服务智能体 | 吸奶器选型、开箱使用、清洁消毒、蓝牙与法兰指导、故障排查和售后工单 |
| 全局能力 | 健康红旗、情绪危机、设备安全、权限、Prompt 防护、上下文账本和 Memory |

## Tool 归属（25）

当前共有 25 个模型可见 Tool Contract。Tool 只有一个领域归属，但公共 Tool 和有界专业能力可以按 Allowlist 暴露给多个智能体。

### 主智能体与公共能力（13）

`maternal_infant_profile_read`、`maternal_infant_profile_update`、`plans_current_read`、`plans_calendar_read`、`plans_task_create_propose`、`plans_task_complete_propose`、`plans_task_update_propose`、`plans_task_delete_propose`、`plans_plan_delete_propose`、`pregnancy_diary_query`、`pregnancy_diary_save`、`pregnancy_diary_delete`、`conversation_history_image_load`

### 产前服务智能体（3）

`pregnancy_plan_workflow`、`hospital_bag_workflow`、`hospital_bag_cart_update`。

其中 `pregnancy_plan_workflow` 是唯一对模型和 App 暴露的孕期计划流程入口，孕期计划采集、表单分析、追问推进和计划生成由该工作流内部 Handler 执行。

`hospital_bag_workflow` 是唯一对模型暴露的待产包采集与生成入口。它优先恢复活动表单；没有活动采集时，仅在已验证事实完整的情况下直接生成，否则创建表单。表单提交后由 runtime 校验 artifact 血缘并确定性续跑同一工具，不增加第二轮模型调用。

## 孕期计划 Workflow Contract

`pregnancy_plan_workflow` 每次只执行一个命令：

`start_or_resume`、`submit_form`、`answer_current`、`edit_answer`、`pause`、`resume`、`abandon`、`generate_plan`。

运行时以用户维度持久化唯一的活动孕期计划，状态不设自动过期时间。每次转换都增加 `revision`、更新一次性 `step_token`，并写入 append-only workflow event；因此新会话和 App 重启后仍可在已完成步骤上继续，也可以修改历史回答并使依赖它的后续步骤失效后重算。

每轮实际使用的消息、成对 Tool call/output、上下文条目引用、裁剪策略和有界 workflow 投影会写入 model-context snapshot，workflow 转换的命令、交互摘要、revision 和失效步骤则留在 append-only event ledger，便于按当时输入重放和审计。完整历史不会在后续每轮重复塞回模型：默认最多选择 64 个上下文条目、约 12,000 token，并为所有活动 workflow 单独保留约 1,200 token 的投影预算。

工作流回复分成两个独立部分：

- `workflow_reply`：仅含工作流 ID、类型、revision 和不透明 step token，用于拒绝重复点击及过期页面提交。
- `workflow_prompt`：仅含当前问题、稳定选项 ID、是否允许当前步骤的专用文字补充、可编辑步骤和允许命令，供 App 渲染，不包含内部状态 ID、token、模型指令或完整状态。

App 的选项点击、表单提交、暂停、恢复和历史修改通过 `pregnancy_plan_command.v1` 结构化指令进入确定性执行路径，不调用模型。普通输入框不附带孕期计划游标，继续作为自由对话；运行时会把有界工作流摘要作为权威上下文提供给模型，使其回答旁支问题但不推进流程，并在回复后重新返回最新 `workflow_prompt`。用户需要针对当前问题自由补充时，由工作流卡片内的专用输入框显式提交，避免把普通旁支问题误记为流程答案。

检测到需要优先处理的医疗安全信号时，安全回复覆盖普通计划回复，workflow 保留当前步骤并进入暂停态；用户后续显式恢复时继续原步骤，不把已采集内容标成失败或清空。

最终 `generate_plan` 仍经过 `pregnancy.plan.create` Action 边界；只有 `write_succeeded=true` 才能声称已生成并同步。

## 待产包 Workflow Contract

`hospital_bag_workflow` 是待产包采集与清单生成的唯一模型可见入口，公开参数只有可选的 `generation_mode` 和 `restart`。原表单创建、表单血缘校验和清单 artifact 生成逻辑保留为内部操作，不再注册为独立 Tool Contract。

runtime 每次调用都注入当前线程的 workflow state、当前消息中已验证的 `hospital_bag_intake` 提交、可用于预填的默认值，以及仅由 verified facts 映射出的已确认值。对话候选事实可以预填，但不参与“信息已完整”的判断。分流规则如下：

- 已有 collecting_intake：优先恢复并重新发送原表单；只有当前提交的 artifact ID 与活动流程一致，且 9 个必填字段完整、有效、无急症信号时才生成清单。
- 没有活动采集：verified facts 完整时直接生成；否则创建表单。
- 已完成：重复调用返回原结果引用，不重复创建清单；用户明确传 `restart=true` 时才重新采集。
- 检测到明确描述为当前症状的急症信号：停止生成，workflow 进入 safety_interruption 暂停态并返回固定就医提示；未来担忧、风险提示和既往病史本身不视为当前急症。

表单提交后的合法续跑由 runtime 在模型调用前确定性执行同一个 `hospital_bag_workflow`，因此不需要第二轮模型来判断或复制表单 JSON。普通旁支对话仍由模型正常回答，活动采集状态保留在 workflow ledger 中；再次进入待产包服务时会重发原 form artifact。该流程使用 `hospital_bag` workflow schema v2。

### 泌乳服务智能体（8）

`maternal_infant_profile_read`、`maternal_infant_profile_update`、`lactation_timeline_read`、`lactation_timeline_manage`、`milk_analysis`、`plans_milk_plan_propose`、`notifications_milk_reminder_propose`、`ibclc_consult_card_create`。

### 泌乳时间线

`lactation_timeline_read` 是过去、当天和未来泌乳日程与实际记录的统一只读入口。它不新增时间线业务表，而是聚合 `plan_tasks`、`feeding_records`、`pumping_records` 和 `growth_records`；喂养或吸奶记录带有 `plan_task_id` 时与对应日程合并，临时记录保持独立。计划时间、实际发生时间和任务完成时间分别返回，未执行计划不得作为奶量事实参与分析。

`lactation_timeline_manage` 是对应的统一写入口，以 `operation=create|update|delete|set_status|reschedule` 和 `item_type=schedule|feeding|pumping|growth` 管理计划日程与实际记录。喂养和吸奶记录可通过 `plan_task_id` 关联并在同一事务中完成对应日程；批量避让重排仍生成预览并走确认边界。底层各类 Record、Plan Task 和重排 Action 保持独立，以保留各自的校验、权限、幂等和审计语义。

### 奶量分析

`milk_analysis` 统一替代分散的奶量摘要、状态、详细分析、问答采集和评估入口：

- `operation=review` 返回确定性快照；`detail_level=summary` 用于轻量状态判断，`detailed` 增加实际喂养、吸奶、宝宝生长、节奏和趋势明细。
- `operation=start_or_resume` 开始或恢复当前用户的耐久六项信息采集；只有显式 `restart=true` 才重置。
- `operation=answer` 只接受当前轮用户原话能够支持的 `observed_answers`，并通过 workflow revision 与 step token 防止过期回复推进状态。
- `operation=evaluate` 仅在采集完成后生成奶量分析卡和计划准入结论；计划仍需由 `plans_milk_plan_propose` 单独提出。

读取与分析只使用实际发生记录，日程的待执行、跳过或仅手动完成状态不计入奶量产出或摄入事实。

### 妈妈和宝宝基础信息

`maternal_infant_profile_read` 与 `maternal_infant_profile_update` 是统一的妈妈和宝宝基础资料读写入口，取代原有的通用 Profile 读写工具。读取工具不返回奶量产出/摄入、历次分娩或完整生长记录历史。

- 通用妈妈档案 `maternal_profiles` 持久化 `delivery_count`、`latest_delivery_method`、`latest_delivery_date`、`has_cesarean_history`；`age` 沿用通用 `user_profiles`。
- 泌乳专用档案 `lactation_profiles` 只持久化 `current_feeding_mode` 等泌乳场景状态。
- 每个宝宝的 `sex_at_birth`、`birth_weight_kg`、`gestational_age_at_birth_days` 沿用通用 `infant_profiles`；最近体重、身高、头围和测量时间来自该宝宝最新一条有效 `growth_records`。
- 通用 `maternal_current_delivery_infants` 关联最近一次分娩的全部宝宝，并用 `birth_order` 提供稳定的非姓名区分；一个宝宝只能归属一个当前分娩摘要。
- `postpartum_days`、`age_days`、`age_months` 以读取当天和实际分娩/出生日期派生，不在数据库重复持久化。
- 当前分娩摘要仍是一人一行，不为每次分娩建立历史明细；奶量工具通过字段级投影读取必要列，并批量取得各宝宝最新一条生长记录。
- Tool 默认使用 `infant_scope=current_delivery` 返回全部当前宝宝；只有通用资料核对或需要选择其他宝宝 `infant_id` 时才用 `infant_scope=all`，避免较早出生的宝宝干扰奶量分析。
- Tool 输出包含妈妈称呼、年龄、预产期、当前分娩与喂养信息，以及每个宝宝的稳定 `infant_id`、姓名、当前分娩关系、出生和最近测量信息。已有 `actual_delivery_date` 或当前宝宝 `birth_date` 时，`estimated_due_date` 固定投影为 `null`，数据库原始值不被删除。
- 更新工具以 `mother`、`infants` 和 `current_infants` 为边界，通过一个 `profile.update` Action 在同一事务中组合现有通用 Profile 与泌乳 Context Service。
- 读写 Tool Contract 均声明包含字段描述的 Input/Output Schema；Tool Executor 在参数进入 Handler 前和结果进入模型上下文前分别执行校验。
- `missing_fields` 与 `data_quality_issues` 返回 `{code, birth_order}` 对象；`code` 是固定枚举，妈妈或集合级问题的 `birth_order` 为 `null`，宝宝级问题使用对应出生顺序，不再返回需要解析的动态路径字符串。

### 设备服务智能体（3）

`devices_guidance`、`pump_models_read`、`support_ticket_propose`。

以上 3 个 Tool 全部直接暴露给设备服务智能体。`devices_guidance` 同时负责按主题读取官方资料和维护一步步开箱流程；`pump_models_read` 从对象存储中的独立型号文档读取与具体业务流程无关的吸奶器产品事实。

#### `pump_models_read` 只读型号资料契约

该工具采用空对象输入，一次返回当前文档内全部型号的价格、适用场景、功能、吸力、续航、重量、噪声、App 支持、单只购买、图片和来源信息，输出 Schema 为 `pump-models.result.v1`。

工具只提供可比较的产品事实，不接收用户偏好、不执行固定打分、不返回推荐型号、推荐话术或购物车同步建议。设备服务智能体读取型号文档后，结合用户在对话中表达的预算、使用场景和偏好自行比较并组织推荐回复。

型号文档以 `pump_models.reference.v1` 结构化 JSON 代码块保存在 Markdown 中，运行时只从对象存储键 `agent-references/device-service/pump-models.md` 读取；不从 `skills/`、Python 常量或本地发布源回退。文档缺失或对象存储不可用时返回 `pump_models_reference_unavailable`，文档编码、结构或字段校验失败时返回 `pump_models_reference_invalid`，禁止继续使用过期的内置目录。

仓库中的 `assets/agent-references/pump-models.md` 仅作为发布源。发布命令会先执行严格 Schema 校验，再上传到固定对象键；应用容器和智能体运行时均以对象存储内容为唯一事实源。`hospital_bag_cart_update` 在明确添加、替换或恢复具体吸奶器型号时复用同一个读取服务，避免推荐目录与购物车商品信息漂移；其他购物车操作不依赖该文档。

推荐和购物车修改是两个独立边界：普通推荐不得修改任何 Workflow、Artifact 或购物车；只有用户在待产包购物车场景中明确要求添加或替换型号时，才单独调用 `hospital_bag_cart_update`。

#### `devices_guidance` 统一契约

必填参数为已确认的 `model` 和 `operation`。当前支持以下操作：

| `operation` | 用途 | 额外参数 | 是否修改流程 |
| --- | --- | --- | --- |
| `read` | 按需读取某一说明书主题或明确步骤 | `topic`、`step` 二选一；可选 `resource_kind`；法兰主题可传 `measured_nipple_mm` | 否 |
| `start_or_resume` | 开始新开箱指导，或恢复当前线程的已有进度 | 无 | 是 |
| `complete_current` | 在用户明确完成当前步骤后，只推进一个主步骤 | 无 | 是 |
| `cancel` | 取消当前开箱指导 | 无 | 是 |

工具统一返回 `device-guidance.result.v1`，包含 `status`、`mode`、`device_model`、`document_version`、`guidance` 和 `workflow`。直接查询的 `mode=direct`、`workflow=null`；连续指导的 `mode=walkthrough`，以 `workflow.current_step` 作为唯一当前步骤。

连续指导沿用内部 `device_unboxing` Workflow。`start_or_resume` 必须幂等恢复已有进度，`complete_current` 每次只持久化推进一步；流程中的临时清洗、蓝牙或当前步骤问题使用 `read`，不得改变 `active_step` 或 `completed_steps`。

## Tool Contract 边界

模型可见 `ToolContract` 只保留实际参与工具选择或执行的字段：`name`、`domain`、`description`、`input_schema`、`output_schema`、`effect_scope` 和 `timeout_seconds`。

`action_type`、`blocking_policy`、`result_dependency`、权限、owner scope 和审计策略不放在 Tool Contract 中。原因是一个统一工具可以按 `operation` 路由多个内部 Action，而权限与审计也不能由模型声明决定：

- Tool Executor 始终等待当前工具完成或超时，并校验输入和已声明的输出 Schema。
- 当前登录用户由 Runtime 注入，模型不能传入或覆盖 owner user id。
- 用户业务写入由 Action Policy 决定是否需要确认，并由 Action Handler 执行。
- Repository / Service 负责 owner scope、字段校验、事务、幂等和审计。
- 启动时对 Action Policy 与 Action Handler 做双向完整性校验，不把内部 Action 绑定复制到模型契约。

## `propose` 语义

当前 `_propose` 只表示向 Action Runtime 提交结构化变更意图，不能表示是否直接写入；实际行为以 `ActionPolicy.requires_confirmation` 为准。

## Tool 之外的能力

Health Web Search 是主智能体的 Provider 能力。

## 跨域边界

| 用户目标 | 归属 |
| --- | --- |
| 孕期事项、准备顺序和待产安排 | 产前服务智能体 |
| 孕期症状、检查、用药和就医判断 | 主智能体健康能力 |
| 奶量、摄入、喂养记录和泌乳计划 | 泌乳服务智能体 |
| 发热、红肿和明显疼痛等身体风险 | 主智能体健康能力优先 |
| 法兰型号、安装、吸力和漏气 | 设备服务智能体 |
| 乳头受伤或明显疼痛 | 主智能体健康能力优先 |
| 待产包商品调整 | 产前服务智能体 |
| 吸奶器型号推荐和比较 | 设备服务智能体 |

## 全局安全

健康、情绪、人身、设备和权限安全策略对所有智能体生效，并可优先中断普通业务流程。
