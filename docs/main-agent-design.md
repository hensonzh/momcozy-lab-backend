# Momcozy 多智能体架构

## 架构

系统由一个主智能体和产前、泌乳、设备三个专业子智能体组成，共享 Agent Runtime、上下文账本、Tool Executor 和业务服务。

## 回复规则

- 通用问题：主智能体直接回复。
- 单一专业意图：主智能体路由，子智能体直接回复。
- 多专业意图：主智能体按依赖顺序调用子智能体，再汇总回复。

主智能体负责通用回答、专业路由和多专业汇总，不持有子智能体的完整专业指令与底层 Tool。

## 运行时链路

1. Runtime 先完成 owner-scoped 会话上下文和活动流程投影。
2. 无 Tool 的结构化语义路由器读取同一份有界上下文，返回一个或多个有序 `service_skill_id`；路由结果非法时
   直接失败，不回退为全量 Tool。
3. 单一意图直接执行目标智能体；模型只收到该智能体的静态 Tool Allowlist。
4. 多意图按路由器给出的依赖顺序执行。后一个智能体可以把前序专业结果作为不可信数据参考，但仍只能调用
   自己的 Tool；最后由无 Tool 的主智能体统一合成一次用户可见回复。
5. 单智能体路由写入 `agent_runs.service_skill_id`；多智能体路由写入主智能体 ID，并通过
   `agent.routing.completed` 事件保存完整、有序的路由列表。

路由评测直接校验持久化的 `service_skill_id`；缺失路由或用主智能体 ID 代替预期专业智能体都会失败，不提供
兼容性放行。

由可信 UI 提交并已通过 runtime 校验的结构化孕期流程命令、待产包表单提交继续走确定性执行路径，不再额外
调用模型路由器；这类路径本身只调用固定场景 Tool，不会暴露全量 Tool，并直接将本轮路由记录为
`birth-prep`。

## 专业指令

三个 `skills/*/SKILL.md` 当前保留为文件化专业规则来源，由 runtime 在启动时读取正文并追加到对应专业智能体的
system prompt。它们不是模型可发现、可选择或可动态加载的 Skill，不注册 `load_skill` 一类 Tool，也不会注入
其他专业智能体。主智能体只使用全局 system prompt。

## Tool 规则

- 模型可见 Tool 使用唯一的 canonical `snake_case` 名称，不保留旧名称或兼容别名。
- Tool `description` 固定先用一句说明“是什么/能做什么”，再用一句“当……时使用”说明调用时机；参数规则
  由 Input Schema 字段描述承载，不在 Tool description 中重复。
- `read` 表示纯读取；`mutate` 表示同一业务资源内包含多种持久化变更；单一变更直接使用 `create`、`update` 或 `delete`；`manage` 仅表示同一能力内的多阶段流程或读写混合操作。
- 同一资源存在多种变更时合并为一个 `mutate` Tool，并通过 `operation` 区分；只有单一变更时，工具名直接表达具体动作。
- 多操作 Tool 的 Input Schema 按具体 `operation` / `command` 拆成封闭对象分支；每个分支只接受当前动作
  所需字段，所有模型输入字段必须描述含义、单位、枚举映射、适用时机和省略条件。
- `idempotency_key` 属于应用与 Runtime 控制面，不出现在任何模型可见 Tool input schema 中。Runtime 使用
  `run_id + action 语义 + 规范化 apply payload` 生成稳定键并复用同一 Action；App/API 请求仍可使用
  `Idempotency-Key` 请求头处理网络重试。
- owner、可信本地日期、locale、timezone、source 和用户确认状态同样属于 Runtime 可信上下文，不作为模型
  输入参数。结构化 UI 专用命令使用独立内部 Input Schema 校验，不注册到模型 SDK Tool。
- `pregnancy_intake_manage.answer_current` 的模型输入不包含 `step_id`；结构化 UI 回复通过内部 Input Schema
  携带并校验 `step_id`，用于拒绝过期的快捷回复，不把应用侧并发控制字段交给模型生成。
- `profile_update` 是单一更新能力，模型只传 `mother`、`infants` 或 `current_infants`，不重复传
  `operation`，也不控制 owner、可信本地日期或幂等键。
- 当前分娩宝宝只认数据库中的明确关系，不根据宝宝档案数量临时推断。普通资料更新同步应用；完整替换
  `current_infants` 必须确认，并在执行时校验提案所见的旧关系，避免覆盖并发修改。
- 实际分娩日期或明确关联的当前宝宝出生日期存在后，数据库中的旧预产期会清空；当前分娩方式为剖宫产时，
  剖宫产史必须为真。成功更新后 Tool 返回重新读取的规范化母婴资料。
- 每个智能体仅接收自己的静态 Tool Allowlist；Tool 调用和结果按 Agent Loop 顺序进入上下文。
- 跨领域计划摘要、日程和已关联实际事实统一从 `schedule_timeline_read` 读取；不再为泌乳、孕期或产后康复分别提供日历读取 Tool。
- 计划日程和实际记录统一由 `schedule_timeline_mutate` 管理，并通过 `entry_type=schedule|execution` 区分；完成 feeding/pumping 泌乳任务必须同时写入实际时间和对应奶量，由实际记录 Action 原子完成关联任务。
- 已持久化的计划统一由 `plan_read` 读取，由 `plan_mutate` 创建、更新和删除；创建时使用 `plan_type` 选择领域构建器，更新和删除以后端读取到的真实计划类型为准。
- `plan_read` 不代替日程时间线读取；计划下的任务、完成状态和实际执行事实仍由 `schedule_timeline_read` / `schedule_timeline_mutate` 管理。
- 日记统一由 `diary_read` / `diary_mutate` 读取和变更；同一用户、同一日期最多一条。孕期、产后恢复和育儿只是
  日记内容语义，不作为资源类型或存储分区；不再提供孕期专用的模型 Tool。
- `pregnancy_intake_manage` 只负责孕期资料采集；采集完成后通过 `plan_mutate` 创建孕期计划，不在一个工具内混合采集与业务资源写入。
- `support_ticket_draft_create` 只创建可编辑售后草稿，不直接提交正式工单；是否获得用户当轮同意由 Runtime
  使用可信当前消息判定，不由模型传布尔值自证。

## 工具输出契约

- 每个工具只返回一份经过 Output Schema 校验的 `canonical_output`；全部注册工具都必须声明 Output Schema。
- `canonical_output` 是模型、运行时判断和工具输出持久化共同使用的业务事实来源。模型默认无损接收完整
  canonical JSON，不再经过模型专用或审计专用的二次投影。
- 图片和文件作为 canonical JSON 之外的结构化媒体块追加，不得替换或删减 canonical JSON。
- 延迟事件等运行时控制信息通过 `ToolResult.deferred_events` 单独传递，不得混入 canonical JSON。
- `agent_tool_outputs.output_json` 保存行内结果；超出行内上限时，完整结果写入对象存储并通过 `output_ref`
  引用。
- `tool.completed` 事件只携带用于界面展示的 `output_summary`，不能作为另一份业务结果；日志脱敏只能发生
  在日志边界，不得改写 canonical output。

## Tool 归属（17）

### 主智能体与公共能力（9）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`、`conversation_history_image_read`

### 产前服务智能体（5）

`plan_read`、`plan_mutate`、`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

### 泌乳服务智能体（8）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`milk_analysis_manage`、`ibclc_consult_card_create`

### 设备服务智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_draft_create`

## 实施约束

当前仅在测试环境供少量内部用户使用，数据可直接清理；方案直接切换，不兼容旧架构、旧 Tool 名称、旧契约或旧测试数据。
