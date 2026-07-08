# MomCozy Multi-Agent Architecture Plan

本文档记录 MomCozy 新版智能体的目标多智能体架构。它用于指导后续重构实现，
重点回答：

1. 主智能体和子智能体分别负责什么。
2. 每轮请求如何调度。
3. 单意图、多意图和通用问答如何处理。
4. skill、工具包、artifact、流式事件和 eval 如何组织。
5. 该方案相比旧版单智能体 skill 方案和当前 specialist routing 方案的取舍。

## 目标判断

推荐采用 **主从多智能体架构**：

```text
User / App
  -> Main Agent
      -> direct answer for general QA
      -> one Scene Agent for single-intent service
      -> multiple Scene Agents in parallel for multi-intent service
           -> Main Agent or Summary Agent merges outputs
  -> application events / assistant reply / artifacts / actions
```

核心原则：

- 每轮请求都先进入主智能体。
- 主智能体负责意图理解、路由、通用问答、缺失信息追问和多结果汇总。
- 子智能体按产品服务场景划分，负责具体服务流程、工具调用、交付物和回复风格。
- 应用侧只提供状态、上下文、权限和 runtime 硬边界，不在智能体之前做复杂意图分支。
- 子智能体拥有自己的 skill 和工具包；工具暴露由后端契约控制，不由前端或自然语言回复推断。

## 为什么不是纯单智能体

旧版单智能体 + 动态 skill 的优点是体验逻辑清楚：

- 全局角色统一。
- 模型可以根据上下文自主选择 skill。
- 服务流程集中在对应 skill 中，回复风格和交付物较稳定。

但它也带来明显工程问题：

- 全局 prompt、SKILL.md、tool result 中存在大量重复指令和潜在冲突。
- 单个 agent loop 过大，服务流程、工具选择、artifact、状态管理混在一起。
- 所有能力最终汇聚到一个智能体上下文，后续多人维护和测试成本高。
- 当服务场景变多时，工具暴露和上下文预算难以控制。

目标多智能体架构保留旧版的“skill + tool”智能体验，同时把业务能力拆到场景子智能体，
让每个子智能体更容易维护、测试和演进。

## 为什么不是应用侧 specialist routing

当前 specialist routing 的工程边界更清楚，但体验上容易变得机械：

- 应用侧或后端 deterministic routing 过重时，模型自主判断空间变小。
- 路由、prompt、tool allowlist、playbook 分散，排查体验问题不直观。
- 对自然语言、多意图、模糊意图的处理容易退化成规则分支。

目标方案中，routing 由主智能体完成。后端仍保留权限、owner scope、tool contract、
action/audit、run ledger、stream contract 等硬边界，但不在模型工作前替模型完成业务意图拆分。

## 智能体角色

### Main Agent

主智能体使用轻量模型，但不能弱到无法稳定路由。它的职责是：

- 识别用户本轮意图。
- 判断是否属于通用问答。
- 判断是否需要一个或多个子智能体。
- 对缺失信息进行追问。
- 将用户请求拆成一个或多个子任务。
- 调用子智能体。
- 对多个子智能体结果进行汇总。
- 统一最终回复的人设、语气、简体中文表达和安全边界。

主智能体不应该直接承担复杂业务工具调用。例外只包括：

- 通用只读资料查询。
- 轻量上下文读取。
- 明确不属于任何子智能体的普通问答。

### Scene Agents

第一批子智能体按服务场景划分：

```text
pregnancy_service_agent
  孕期计划、待产包、分娩沟通卡、孕期日记。

lactation_agent
  奶量分析、泌乳建议、吸奶记录、吸奶计划、IBCLC 支持前置判断。

postpartum_recovery_agent
  产后恢复、恢复计划、情绪陪伴、恢复期提醒。

device_after_sales_agent
  设备使用指导、故障排查、售后支持、保修/工单。

general_support_agent
  产品介绍、非业务闲聊、无法归类但安全的基础问答。
```

每个子智能体必须拥有：

- 独立 `SKILL.md`。
- 明确服务范围。
- 明确不负责的范围。
- 工具包 allowlist。
- artifact contract。
- 回复风格和交付物要求。
- 主流程 eval cases。

## 每轮请求流程

### 1. 创建 run

FastAPI 接收 App 请求，创建：

- `agent_threads`
- `agent_runs`
- `agent_messages`
- `agent_events`

并把 run 交给 agent worker 执行。

### 2. 构造上下文

后端为本轮构造上下文投影：

- stable system prompt。
- 主智能体说明。
- skill manifest：只包含可用子智能体的 id、name、description、scope。
- selected conversation history。
- recent run facts。
- memory projection。
- fresh business facts。
- runtime context：当前时间、用户 locale、基础位置、可用能力、附件摘要。

注意：业务事实来自业务表，context projection 只是本轮给模型看的投影，不是权威状态。

### 3. 主智能体决策

主智能体输出结构化 plan：

```json
{
  "mode": "direct_answer | single_agent | multi_agent | clarification",
  "intents": [
    {
      "agent": "pregnancy_service_agent",
      "task": "create_hospital_bag_intake_form",
      "confidence": 0.87,
      "reason": "用户想准备待产包，需要孕期服务智能体处理"
    }
  ],
  "needs_clarification": false,
  "clarifying_question": null
}
```

主智能体可以直接回复的情况：

- 用户寒暄。
- 简单产品说明。
- 不需要读取业务数据或调用工具的普通问答。
- 明确无法提供服务但可以解释边界的问题。

### 4. 单意图执行

当 plan 为 `single_agent`：

```text
Main Agent
  -> target Scene Agent
  -> target Scene Agent uses its skill + tools
  -> target Scene Agent returns final answer + artifacts/actions
  -> final answer streamed to App
```

单意图场景下，最终回复直接采用目标子智能体的回答。主智能体只做必要的安全和格式收口，
不要二次改写到丢失子智能体服务风格。

### 5. 多意图执行

当 plan 为 `multi_agent`：

```text
Main Agent
  -> split tasks
  -> run independent Scene Agents in parallel
  -> wait for required outputs
  -> Summary step merges answers
  -> final answer + artifacts/actions streamed to App
```

并行前要判断依赖关系：

- 可并行：奶量分析 + 产后恢复建议。
- 有依赖：先生成待产包清单，再更新购物车。
- 需追问：用户只说“帮我安排一下”，但没有明确场景。

多意图子智能体返回结构化结果：

```json
{
  "agent": "lactation_agent",
  "status": "completed",
  "answer": "这里是奶量分析结果...",
  "artifacts": [],
  "actions": [],
  "facts_used": [],
  "followups": []
}
```

汇总智能体或主智能体负责：

- 去重。
- 排序。
- 合并行动建议。
- 保留各子智能体 artifact。
- 保持最终回复简短、自然、稳定。

## Skill 设计

保留标准 skill 结构：

```text
skills/
  pregnancy-service/
    SKILL.md
  lactation/
    SKILL.md
  postpartum-recovery/
    SKILL.md
  device-after-sales/
    SKILL.md
  general-support/
    SKILL.md
```

`SKILL.md` 只负责该场景内部体验：

- 服务目标。
- 触发场景。
- 必要信息收集规则。
- 工具使用顺序。
- artifact 生成规则。
- 回复风格。
- 边界和升级。

不要在 `SKILL.md` 中重复全局人设、全局医疗安全免责声明、全局工具契约或后端权限规则。
这些由全局 prompt 和后端 runtime contract 统一负责。

## 工具包设计

每个子智能体绑定自己的工具包：

```text
pregnancy_service_agent tools:
  profile.read
  pregnancy.plan_context.read
  birth_plan_form_create
  labor_communication_card_create
  hospital_bag_form_create
  hospital_bag_card_create
  hospital_bag_cart_update
  diary.entry_upsert.propose

lactation_agent tools:
  records.milk_summary.read
  records.feeding_record.propose
  records.pumping_record.propose
  plans.milk_plan.propose
  notifications.milk_reminder.propose
  support.ticket.propose

postpartum_recovery_agent tools:
  profile.read
  diary.recent.read
  plans.task_create.propose
  artifacts.postpartum_checkin.create
  memory.create.propose

device_after_sales_agent tools:
  devices.pump_status.read
  devices.guidance_assets.read
  files.vision_summary.read
  support.ticket.propose
```

工具包不是自然语言说明，而是后端 `ToolContract` 的显式集合。每个工具仍必须声明：

- read/write。
- permission。
- owner scope。
- side effect level。
- blocking policy。
- timeout。
- safe result policy。
- audit/idempotency 要求。

## Artifact 和 Action

Artifact 用于本轮可见交付物：

- 待产包信息采集表单。
- 待产包清单卡。
- 分娩沟通卡。
- 奶量分析卡。
- 产后恢复 check-in。
- 设备指导步骤卡。

Action 用于业务写入：

- 创建计划。
- 创建任务。
- 写入日记。
- 更新购物车。
- 创建工单。
- 记录喂养/吸奶数据。

低风险且不影响下一步推理的写入可以进入 outbox effect lane。中高风险写入仍走确认、
审计和幂等控制。即使产品当前不希望出现显式二次确认，也不能让模型直接绕过后端
权限、owner scope 和 audit。

## 流式体验

App 只消费 application events：

```text
run.queued
run.started
run.progress
message.delta
message.completed
tool.started
tool.completed
artifact.created
action.queued
action.applied
run.completed
```

主智能体和子智能体执行过程中都应发出可读进度：

- 正在理解你的问题
- 正在整理相关信息
- 正在交给孕期服务处理
- 正在生成待产包信息采集表单
- 正在整理回复

token delta 可通过 Redis transient stream 提供实时打字体验；权威最终内容仍以
`message.completed` 为准。

## 模型配置

建议模型分层：

```text
Main Agent:
  轻量模型，负责路由、通用问答、追问和汇总。

Scene Agents:
  可按场景选择更强模型，负责工具调用和复杂服务交付物。

Judge / Eval:
  独立配置，不与线上回复模型强绑定。
```

模型供应商和模型名必须通过环境变量或配置项控制。不能把 OpenAI、Minimax 或任何
provider 写死在业务逻辑中。

## 缓存与上下文

为了提高 prompt cache 命中率：

- stable system prompt 固定在最前。
- 主智能体 instruction 固定。
- skill manifest 尽量稳定，只放 id、name、description、scope。
- 子智能体完整 skill 只在被调用时注入对应 agent，不放入主智能体全局上下文。
- 每轮动态内容放在后面：history、runtime context、business facts、current user message。

不要把所有子智能体的完整 `SKILL.md` 都塞进每轮主智能体上下文。

## 状态与持久化

权威状态分层：

```text
Business state:
  业务表中的计划、日记、奶量记录、设备状态、购物车、工单。

Runtime ledger:
  thread、run、message、tool_call、event、artifact、action、routing decision。

Transient state:
  Redis 中的 active lock、cancel flag、live delta、短期进度。

Context projection:
  每轮请求临时构造给模型看的上下文投影，不是持久状态层。
```

主智能体和子智能体之间的中间结果应写入 runtime ledger 或 graph state，不写入业务表，
除非通过明确 action/tool 完成业务副作用。

## 安全与硬边界

即使采用模型主导路由，也必须保留应用侧硬边界：

- 用户身份和 owner scope。
- tool permission。
- 文件归属校验。
- prompt injection 防护。
- 医疗和情绪危机场景 hard gate。
- action/audit/idempotency。
- rate limit 和成本预算。

模型可以决定“应该怎么服务”，不能决定“是否绕过权限或审计”。

## Eval 验收

每个子智能体至少覆盖：

- 单意图主流程。
- 多轮信息收集。
- 工具选择正确性。
- artifact 生成正确性。
- action 生命周期。
- 安全红旗。
- 权限越权。
- 多意图协作。
- replay 后 UI 可恢复。

主智能体额外覆盖：

- 通用问答直接回复。
- 单意图路由。
- 多意图拆分。
- 需要追问时不误调用工具。
- 模糊意图不强行路由。
- 多子智能体结果汇总不丢 artifact/action。

## 迁移步骤

### Phase 1: 固定目标 contract

- 定义 `MainAgentPlan` schema。
- 定义 `SceneAgentResult` schema。
- 定义子智能体 registry。
- 定义 agent -> tool package 映射。
- 定义 progress event 文案集合。

### Phase 2: 重构当前 routing

- 删除不再使用的应用侧复杂 specialist routing。
- 保留 deterministic safety、permission、runtime guard。
- 将路由决策改为主智能体结构化输出。
- routing decision 继续写入 runtime ledger，便于调试和 eval。

### Phase 3: 子智能体 skill 化

- 为每个场景建立标准 `SKILL.md`。
- 从旧版 skill 中迁移体验语义、服务流程和交付物要求。
- 删除迁移后不再使用的 playbook 或重复 prompt。

### Phase 4: 子智能体工具包

- 按场景绑定 tool allowlist。
- 补齐 tool contract 缺口。
- 保证工具结果只返回事实、资源 ID、artifact/action 状态，不返回额外提示词。

### Phase 5: 单意图闭环

优先实现：

1. 待产包信息采集表单。
2. 奶量分析。
3. 设备指导。
4. 产后恢复 check-in。

每个闭环必须包含：prompt、skill、tools、artifact、stream event、eval、App 渲染验证。

### Phase 6: 多意图并行和汇总

- 支持独立任务并行。
- 支持依赖任务串行。
- 支持汇总模型。
- 支持多 artifact 合并展示。

### Phase 7: 体验对齐

- 状态条与旧版 loop 阶段语义对齐。
- 流式回复可见。
- 语音播报链路打通。
- artifact 表单、卡片、购物车与旧版体验对齐。

## 当前未解决问题

截至本文档创建时，以下体验问题仍需代码修复：

- AI 回复前状态条未实时展示后端状态信息。
- 回复看起来不像流式输出。
- 语音播放功能未完整打通。
- 输入框左下仍出现播放状态提示。
- 待产包信息采集表单未正确渲染为表单。

这些问题属于 Phase 7 的体验对齐工作，不是本文档中的架构分析本身已经完成的代码修复。
