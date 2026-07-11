# MomCozy Multi-Agent Architecture Plan

本文档记录 MomCozy 新版智能体的目标多智能体架构。它用于指导后续重构实现，
重点回答：

1. 主智能体和子智能体分别负责什么。
2. 每轮请求如何调度。
3. 单意图、多意图和通用问答如何处理。
4. skill、工具包、artifact、流式事件和 eval 如何组织。
5. 该方案相比旧版单智能体 skill 方案和当前 specialist routing 方案的取舍。

## 目标判断

推荐采用 **主从多智能体架构**，但按阶段推进。第一阶段只引入多智能体外壳，
不拆散当前已经跑通的单智能体能力：

```text
User / App
  -> main_coordinator_agent
      -> passthrough delegate_to_cozymate
      -> cozymate_service_agent
           -> current single-agent implementation
           -> existing service skills
           -> existing tool/action/artifact contracts
  -> application events / assistant reply / artifacts / actions
```

核心原则：

- 每轮请求都先进入主智能体。
- 当前阶段主智能体是纯透传节点，不做意图判断、通用问答、边界说明或缺失信息追问。
- 第一阶段只有一个真实子智能体：`cozymate_service_agent`，它直接沿用当前单智能体。
- `cozymate_service_agent` 继续负责具体服务流程、工具调用、交付物和回复风格。
- 应用侧只提供状态、上下文、权限和 runtime 硬边界，不在智能体之前做复杂意图分支。
- 未来再按产品场景从 `cozymate_service_agent` 中逐步拆出更细的子智能体。
- 工具暴露由后端契约控制，不由前端或自然语言回复推断。

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

目标多智能体架构保留旧版的“skill + tool”智能体验，但不要一开始就把所有业务能力拆成多个
scene agents。迁移期先把当前单智能体包装为 `cozymate_service_agent`，由主智能体委派；
当某个产品场景的 prompt、工具、artifact、eval 已经足够稳定，再从中剥离为独立子智能体。

## 为什么不是应用侧 specialist routing

当前 specialist routing 的工程边界更清楚，但体验上容易变得机械：

- 应用侧或后端 deterministic routing 过重时，模型自主判断空间变小。
- 路由、prompt、tool allowlist、playbook 分散，排查体验问题不直观。
- 对自然语言、多意图、模糊意图的处理容易退化成规则分支。

目标方案中，routing 由主智能体完成。后端仍保留权限、owner scope、tool contract、
action/audit、run ledger、stream contract 等硬边界，但不在模型工作前替模型完成业务意图拆分。

## 智能体角色

### Main Agent

当前阶段主智能体不是 LLM agent，而是纯透传节点。它的职责是：

- 接收 runtime 传入的当前请求。
- 固定产出 `delegate_to_cozymate` / `passthrough` plan。
- 把请求交给 `cozymate_service_agent`。
- 在 runtime ledger 中记录主从边界。

当前阶段主智能体不做：

- 意图识别。
- 通用问答。
- 缺失信息追问。
- 安全判断。
- 最终回复改写。

将用户请求拆成多个子任务、调用多个子智能体、对多个子智能体结果进行汇总，属于后续阶段能力。

主智能体不直接承担任何业务工具调用。安全、权限、owner scope 和 tool contract 仍由
runtime guard 与工具执行层负责。

### Scene Agents

第一阶段只启用一个子智能体：

```text
cozymate_service_agent
  当前单智能体原样迁入。
  继续加载现有 service skills。
  继续使用现有 tool registry / tool executor。
  继续生成现有 actions / artifacts / replies。
```

它的职责是：

- 承接大多数产品服务请求。
- 保留当前 CozyMate 统一语气和服务体验。
- 复用当前已验证的 service skill、工具、action、artifact、stream event 和 eval。
- 作为 Main Agent 的委派目标，而不是继续承担全局多智能体编排。

后续阶段再按服务场景逐步拆出独立子智能体：

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

这些细分子智能体不是第一阶段交付物。拆分条件是：对应场景已有稳定 prompt、工具包、
artifact/action contract、主流程 eval 和 App 展示方式。

每个最终独立子智能体必须拥有：

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

当前阶段为了保持旧方案行为等价，模型可见的 `runtime_context.state` 继续使用旧兼容语义：
`agent_mode=single_main_agent`、`execution_mode=single`。`main_coordinator_passthrough`、
`delegated_agent=cozymate_service_agent`、`routing_source=passthrough` 等新架构字段只写入
runtime ledger、checkpoint 和调试投影，不进入模型输入。这样可以先重构状态机和审计边界，
避免因为架构命名变化影响模型回复和 eval。

### 3. 主智能体透传

当前阶段主智能体输出固定结构化 plan：

```json
{
  "mode": "delegate_to_cozymate",
  "execution_mode": "passthrough",
  "intents": [
    {
      "agent": "cozymate_service_agent",
      "task": "cozymate_service_request",
      "confidence": 1,
      "reason": "当前阶段主智能体不做判断，所有请求透传给 CozyMate 服务智能体"
    }
  ],
  "needs_clarification": false,
  "clarifying_question": null
}
```

`direct_answer`、`clarification` 和 `safety_blocked` 都是后续阶段能力。当前阶段即使是寒暄或
边界说明，也先透传给 `cozymate_service_agent`。

### 4. 第一阶段委派执行

当 plan 为 `delegate_to_cozymate`：

```text
Main Agent
  -> cozymate_service_agent
  -> current single-agent implementation uses existing service skills + tools
  -> cozymate_service_agent returns final answer + artifacts/actions
  -> final answer streamed to App
```

第一阶段最终回复直接采用 `cozymate_service_agent` 的回答。主智能体只做必要的安全和格式收口，
不要二次改写到丢失当前 CozyMate 服务风格。

### 5. 后续多意图执行

多意图并行不是第一阶段目标。等至少两个独立 scene agents 从 `cozymate_service_agent` 中拆出后，
再支持：

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

## Agent 目录设计

当前代码先按真实子智能体收拢，而不是按横切技术层继续堆在
`agent_runtime/` 根目录：

```text
app/modules/agent_runtime/
  agents/
    main_coordinator_agent/
      schemas.py
      planner.py
    cozymate_service_agent/
      prompts/
      skills/
      tools/
      context/
      skill_registry.py
  graphs/
  run_lifecycle/
  event_stream/
  memory/
  safety/
```

- `agents/main_coordinator_agent/` 放主智能体的 plan schema 和第一阶段最小 planner。
- `agents/cozymate_service_agent/` 放当前单智能体的 system prompt、service
  skills、tool schema、tool contract、tool handler 和 business facts projection。
- `graphs/`、`run_lifecycle/`、`event_stream/`、`memory/`、`safety/` 保持为多智能体共用 runtime 能力。
- 后续如果从 CozyMate 中拆出独立子智能体，应新增
  `agents/<new_agent_name>/`，而不是继续把 prompt、skill、tool handler 加到
  runtime 根目录。

## Skill 设计

第一阶段保留 CozyMate 子智能体内部的标准 skill 结构：

```text
agents/cozymate_service_agent/skills/
  birth-prep/
    SKILL.md
  milk-management/
    SKILL.md
  health-consultation/
    SKILL.md
  emotion-support/
    SKILL.md
  device-guidance/
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

当前测试门禁要求 service skill 不重新声明全局 prompt ownership，例如全局规则、全局人设、
CozyMate 身份、默认回复长度、快捷输入总规则、工具结果总规则和 runtime 边界。skill 可以写
场景回复规则，但不能把全局规则复制进来。

## 工具包设计

第一阶段仍由 `cozymate_service_agent` 暴露工具，但工具不再作为一组扁平自然语言能力描述。
后端从 `ToolContract.domain` 派生 Responses API 风格 namespace，并在 SDK request contract 中记录：

- `tool_namespaces`：按 `records`、`plans`、`devices`、`support` 等领域分组。
- `tool_search_enabled`：当 namespace 中存在 deferred tools 时启用。
- `tools`：contract name 继续映射到真实 handler；OpenAI provider 在 `tool_search_enabled`
  时走 Responses namespace adapter，非 Responses provider（例如 Minimax）走 flat `FunctionTool`
  兼容层并且不标记 deferred loading。

典型 namespace 如下：

```text
birth_prep namespace:
  pregnancy.plan.propose
  plans.plan_delete.propose
  plans.task_update.propose
  plans.task_delete.propose
  birth_plan_form_create
  labor_communication_card_create
  hospital_bag_form_create
  hospital_bag_card_create

milk_management namespace:
  records.milk_status.read
  records.milk_summary.read
  records.milk_analysis.read
  records.growth.read
  records.feeding_record.propose
  records.pumping_record.propose
  records.feeding_record_delete.propose
  records.pumping_record_delete.propose
  records.growth_record.propose
  records.growth_record_update.propose
  records.growth_record_delete.propose
  plans.current.read
  plans.calendar.read
  plans.milk_plan.propose
  plans.task_create.propose
  plans.task_complete.propose
  notifications.milk_reminder.propose

health_consultation namespace:
  diary.entry_upsert.propose
  ibclc_consult_card_create

device_support namespace:
  devices.pump_status.read
  devices.guidance_assets.read
  support.ticket.propose

global eager tools:
  load_service_skill
  profile.read
  profile_update
  images.inspect
```

namespace 不是 prompt 里让模型“加载旧工具”的自然语言指令，而是后端 `ToolContract` 的显式集合。
service skill 可以引用当前 namespace / contract，但不能引用未注册的旧工具名，例如
`milk_status_query`、`milk_analysis_intake_manage`、`device_manual_search`。每个工具仍必须声明：

- read/write。
- eager/deferred loading mode。
- side effect level。
- blocking policy。
- timeout。
- safe result policy。
- audit/idempotency 要求。

模型可见 contract 不携带逐工具权限或 owner 字段；当前 actor 与 owner scope
由 runtime/service 统一派生和校验。

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
  当前阶段不调用模型，只做 passthrough delegate_to_cozymate。
  后续阶段再升级为轻量模型，负责路由、通用问答、追问和汇总。

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

第一阶段 `cozymate_service_agent` 至少覆盖：

- 单意图主流程。
- 多轮信息收集。
- 工具选择正确性。
- artifact 生成正确性。
- action 生命周期。
- 安全红旗。
- 权限越权。
- replay 后 UI 可恢复。

eval 口径要分层：

- wrapper/delegated agent：第一阶段真实 run 级执行者是 `cozymate_service_agent`。
- scene service skill：seed case 仍可描述 `milk-management`、`device-guidance` 等期望场景。

因此 runtime eval 不能把 run 级 `cozymate_service_agent` 直接判成 scene skill routing mismatch；
只有当可观测 scene skill 选择出现错误，或后续拆出独立 scene agents 后，才按 scene skill 严格比较。

第一阶段 Main Agent 覆盖：

- 所有请求固定透传到 `cozymate_service_agent`。
- ledger 中记录 `delegate_to_cozymate` / `passthrough`。
- 不做通用问答、追问、模糊意图判断或直接回复。

后续主智能体升级为轻量 LLM 或拆出多个独立 scene agents 后，再增加：

- 通用问答直接回复。
- 需要追问时不误调用工具。
- 模糊意图不强行路由。
- 多意图拆分。
- 多子智能体协作。
- 多子智能体结果汇总不丢 artifact/action。

## 迁移步骤

### Phase 1: 固定目标 contract

- 定义 `MainAgentPlan` schema。
- `MainAgentPlan.mode` 当前阶段只允许 `delegate_to_cozymate`。
- 主智能体 planner 是纯透传，不调用 LLM，不读取 prompt，不做条件分支。
- 定义 `cozymate_service_agent` 的子智能体入口，但内部直接复用当前单智能体实现。
- 定义最小 agent registry：`main_coordinator_agent` 和 `cozymate_service_agent`。
- 定义 progress event 文案集合。

### Phase 2: 建立 Main Agent 外壳

- 保留 deterministic safety、permission、runtime guard。
- 将当前 routing 决策收敛为固定 Main Agent 透传输出。
- 所有请求先输出 `delegate_to_cozymate`。
- 通用闲聊、边界说明、低风险直接问答和澄清都先交给 `cozymate_service_agent`。
- routing decision 继续写入 runtime ledger，便于调试和 eval。

### Phase 3: 包装当前单智能体为子智能体

- 新增 `cozymate_service_agent` runner 或 adapter。
- `cozymate_service_agent` 内部继续加载当前 service skills。
- `cozymate_service_agent` 内部继续使用当前 tool registry / tool executor。
- `cozymate_service_agent` 继续产出现有 action、artifact、message 和 stream events。
- 不在本阶段拆分 lactation、birth prep、device、postpartum 等独立子智能体。

### Phase 4: 收敛工具暴露

- Main Agent 不暴露业务写工具。
- `cozymate_service_agent` 暂时沿用当前工具集合，但以 namespace + deferred tool loading 的 request contract 暴露。
- 常用读取工具保持 eager；写入、artifact、support 等低频或高风险工具标记为 deferred，由 tool search 加载。
- OpenAI provider 使用 Responses API 的 namespace/tool_search adapter 执行这一 contract；不支持该能力的 provider
  保持 flat tools 兼容执行，但 runtime metadata 中 `tool_search_enabled=false`。
- 补齐当前工具 contract 缺口。
- 旧方案中的精细颗粒度读写能力必须落到当前 contract：奶量分析快照、生长记录读写、计划日历读取、计划任务更新/删除、计划删除、记录删除、奶量计划预览和 IBCLC 咨询卡片都应有注册工具、handler、action policy 和 outbox apply 路径。
- 保证工具结果只返回事实、资源 ID、artifact/action 状态，不返回额外提示词。
- 工具输出进入模型前递归剥离 `assistant_instruction`、`prompt_hint`、`response_contract`、
  `system_prompt` 等指令型字段；工具结果只能作为事实、状态、候选数据、artifact/action ID
  或缺失字段摘要使用，不能成为隐藏提示词渠道。

### Phase 5: 单意图闭环

优先验证当前单智能体作为子智能体后的闭环：

1. 待产包信息采集表单。
2. 奶量分析。
3. 设备指导。
4. 产后恢复 check-in。

每个闭环必须包含：prompt、skill、tools、artifact、stream event、eval、App 渲染验证。

### Phase 6: 后续拆分独立子智能体

只有当某个场景满足以下条件时，才从 `cozymate_service_agent` 中拆出独立子智能体：

- 场景 prompt 和服务流程稳定。
- 工具包 allowlist 清晰。
- artifact/action contract 稳定。
- 主流程 eval 覆盖完整。
- App 已经能稳定渲染该场景交付物。

拆分优先级仍按产品价值排序：

1. 待产包 / 分娩沟通。
2. 奶量管理。
3. 设备指导。
4. 产后恢复。

### Phase 7: 多意图并行和汇总

- 支持独立任务并行。
- 支持依赖任务串行。
- 支持汇总模型。
- 支持多 artifact 合并展示。

### Phase 8: 体验对齐

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
