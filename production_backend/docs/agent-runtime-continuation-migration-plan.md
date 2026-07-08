# Agent Runtime Continuation Migration Plan

本文档描述新后端中智能体部分的后续迁移计划。它衔接
`backend-refactor-execution-plan.md`，重点回答：

1. 当前 agent runtime 已完成到什么程度。
2. 接下来如何把 runtime 底座推进成完整的 MomCozy 业务智能体。
3. 每个阶段如何拆 PR、如何测试、如何验收。
4. 什么时候才能判断“智能体重构完成”。

## 当前基线

新后端已经完成的是 **production agent runtime foundation**，不是完整的
业务智能体产品。

已具备：

- 独立的 `production_backend/app/modules/agent_runtime/` 模块。
- thread / run / message / tool call / event / action / artifact /
  checkpoint / safety / eval case 等持久化账本。
- `CurrentUser` owner scope、request id、idempotency、audit、outbox 和
  Redis run controls。
- `/v1/agent/threads`、`/v1/agent/runs`、events、SSE stream、cancel、
  action confirm/reject、admin replay/eval seed 等 API。
- OpenAI Agents SDK runner 边界。
- tool contract / tool executor / action policy 的基础实现。
- action confirmation -> outbox apply 的 effect lane。
- deterministic safety gate、replay bundle、eval seed 基础。
- `AgentRuntimeGraphRunner` 已通过 LangGraph `StateGraph` 执行
  `load_context -> safety_gate -> route_specialist -> sdk_reasoning ->
  tool_result_review -> action_policy -> confirmation_interrupt/final_response ->
  finish`。

截至 2026-07-04 的最新增量：

- 奶量管理已具备 read/action/outbox/eval 的首个完整垂直闭环：
  `records.milk_summary.read`、feeding/pumping record proposal、
  milk plan proposal、milk reminder proposal。
- 孕期计划和日记已具备核心 action proposal：
  `pregnancy.plan_create.propose`、`plans.task_create.propose`、
  `plans.task_complete.propose`、`diary.entry_upsert.propose`。
- 计划、任务、日记、提醒、奶量记录的 action apply handler 已接入 outbox
  worker。
- SDK runner 已有 provider error -> stable error code 的映射。
- deterministic safety gate 已覆盖更多母婴健康和情绪危机红旗表达。
- product eval seed 已覆盖孕期计划、任务完成、日记写入和当前 support ticket
  tool contract。
- 长期记忆已有后端底座：`agent_memories` schema/repository/service、
  `memory.create.propose` tool、`agent.memory.create` action/outbox handler，
  每轮 bounded `memory_projection` 上下文投射，以及用户可见的
  `GET/DELETE /v1/agent/memories` 管理 API。
- eval harness 已具备 seed schema 校验、memory preference seed case、
  deterministic assertion engine、replay bundle -> eval trace 转换，以及
  `scripts/run_agent_replay_eval.py` 最小 CLI。
- eval assertion engine 已覆盖 safety-only 流程的 side-effect 禁止断言，
  可拦截红旗/注入场景中的写 proposal 或 action 泄漏。
- 设备指导已有 `devices.guidance_assets.read` 只读工具，可读取打包的
  device-guidance asset metadata，避免继续依赖旧的 `device_reference_lookup`
  占位 contract。
- IBCLC/professional support eval 已对齐到真实 `support.ticket.propose`
  handoff contract，不再要求不存在的 `ibclc_consult_proposal`。
- 场景专家 routing 已有后端基础：runtime 先通过
  `SpecialistRoutingService` 生成 routing plan，再选择
  `general_assistant`、`pregnancy_service`、`lactation`、
  `postpartum_recovery`、`after_sales` 或 `safety_guardrail` profile。routing
  decision 会写入 `agent_routing_decisions`，并把摘要写入 `agent_runs`、
  context projection、checkpoint 和 SDK trace metadata；specialist profile
  使用显式 tool contract allowlist，避免把无关工具暴露给当前 run。
- 低风险、不影响下一步推理的写操作已支持 `enqueue_and_continue`：
  feeding record、pumping record、hospital-bag cart update 会创建 action、
  立即确认并进入 outbox effect lane，不再让 run 进入
  `waiting_for_confirmation`。中高风险 action 仍走 confirmation。
- 场景专家不再只是 tool allowlist。`app/modules/agent_runtime/skills/`
  已把旧版 service skill
  的核心体验语义沉淀为版本化 service playbook，并在每轮 SDK instructions 和
  context projection 中注入 `service_playbook_id/version/scope/deliverables`。
  当前 playbook 覆盖 `pregnancy_service`、`lactation`、
  `postpartum_recovery`、`after_sales`、`safety_guardrail` 和
  `general_assistant`。
- 全局智能体角色已恢复为 CozyMate：温柔、稳定、简短、中文用户使用简体中文，
  但不恢复旧版动态 `load_skill`，也不保留 Responses API loop 兜底。
- 孕期服务、泌乳和产后恢复已具备第一批服务交付物 artifact tool：
  `artifacts.hospital_bag_card.create`、
  `artifacts.labor_communication_card.create`、
  `artifacts.lactation_summary.create`、
  `artifacts.postpartum_checkin.create`。这些工具创建 owner-scoped
  `agent_artifacts` 并发出 `artifact.created` event；它们用于本轮服务产物，
  不替代保存计划、任务、日记、购物车或工单等业务 action。
- 自然语言路由已补强旧 skill 关键触发语义：IBCLC/含乳/乳头疼归泌乳服务，
  待产包/入院包/分娩沟通单归孕期服务，缺件/保修/烧焦/冒烟归设备售后；
  母婴安全红旗的中文表达同步到 routing 和 deterministic safety gate。

仍未完成或后续产品化：

- OpenAI Agents SDK 已有 adapter 边界、settings/tracing metadata 和
  provider-backed eval harness；真实运营 handoff 仍需后续产品化。
- 待产包、奶量、孕期计划、日记、设备指导、支持工单、健康/情绪安全和长期
  记忆均已有新 tool/action/eval contract 的后端基础闭环；图片/语音等体验可
  在 Flutter integration 或后续产品需求中继续补齐。
- 旧 skill 语义已迁成 service playbook，但旧版部分高度定制算法仍未逐项复制，
  例如待产包商品推荐细则、Air1 分步图片指导细节、完整奶量计划算法和运营侧
  IBCLC 接通流程。后续应按产品主流程逐项迁入对应 service/tool，而不是恢复
  旧 `load_skill` 机制。
- 设备指导已有 owner-scoped 状态读取和 guidance assets 读取；更细的
  model-specific 问答策略仍属于产品内容/资料治理工作。
- IBCLC/professional support 已使用 `support.ticket.propose` handoff；独立
  专业支持模块和真实客服/IBCLC 运营闭环仍需后续接入。
- 长期记忆已具备敏感写入拒绝、TTL、禁用 API 和 eval；用户可见管理 UX
  仍需和 Flutter 端一起设计。
- eval 已有 seed runner、replay runner、isolated runtime client、CI smoke、
  JUnit report、报告 artifact upload，以及 provider-backed nightly/manual
  workflow 入口；真实 provider eval 仍需配置凭证、成本预算和环境后运行。
- Flutter 新端尚未和新 agent event/action contract 做端到端联调。

## 目标状态

智能体重构完成时，应满足以下目标。

### Runtime

- 仅使用 `langgraph_sdk` runtime pattern。
- LangGraph 负责 durable orchestration、checkpoint、interrupt、resume、
  timeout、retry、cancel。
- OpenAI Agents SDK 在 graph node 内负责模型/tool loop、specialist agent、
  guardrail 和 tracing。
- 不保留旧 Responses API loop、`previous_response_id`、旧 `ChatSession`
  adapter 或兼容兜底路径。

### State

- Business state 存在业务表。
- Runtime ledger 存在 agent runtime 表。
- Graph checkpoint 只保存 workflow progress，不保存不可丢业务事实。
- Redis 只保存 active run lock、cancel flag、stream cursor、短期 stream delta
  和短期 cache。
- Context projection 每轮由 message ledger、business facts、memory 和当前
  run state 派生，不作为权威状态持久化。

### Tools And Actions

- 每个 tool 都有正式 contract：name、schema、permission、owner scope、
  side effect level、blocking policy、timeout、safe args/result、audit。
- read tool 可直接返回安全摘要。
- write tool 必须声明 blocking policy：read 工具 `must_wait`，低风险且不影响
  下一步推理的写工具 `enqueue_and_continue`，中高风险写工具
  `wait_for_confirmation`。
- 中高风险 action 必须走 `preview -> confirmation -> apply -> audit`。
- 低风险 direct action 仍必须创建 action、audit/outbox 记录和 application
  event，只是跳过用户确认等待态。
- 不影响下一步推理的副作用进入 outbox effect lane。

### Streaming

- 前端只消费 application event，不消费 provider raw event。
- Postgres persisted event 是权威账本；`sequence` cursor 只用于持久化事件
  replay。
- live follow mode 可额外发送 Redis 短期 `message.delta` transient event，
  用于 token 级打字体验；这些 delta 不写入 Postgres，不参与 `sequence`，
  只带 `event_id=delta:<redis-stream-id>` 和 Redis `cursor`。
- reconnect 后客户端用 `after_sequence` 继续拉取权威事件；短期 delta 允许
  在 TTL 内重放或丢失，最终 UI 以 persisted assistant
  `message.completed.payload.text` 为准。

### Eval

- 每个业务主流程都有体验主流程 eval、工具选择 eval、action 生命周期 eval、
  安全红旗 eval、权限越权 eval、stream/replay eval。
- 每个线上异常可导出 redacted replay bundle 并转成 regression eval case。

## 迁移原则

- 先完成一个垂直业务主流程，再迁下一个流程。
- 每个 PR 只改变一个 durable boundary：graph、tool、action、eval、API、
  worker 或文档契约。
- 不为了复用旧逻辑而引入旧 adapter。必要时复制业务规则到新 service/tool，
  然后删除旧路径依赖。
- 后端先独立完成并通过测试；Flutter 集成作为后续 contract verification。
- 每个阶段先写验收标准和测试，再实现。

## Phase 0: Re-baseline And Gap Inventory

目标：把“还缺什么”固定下来，避免继续凭感觉推进。

交付物：

- 当前 agent runtime gap inventory。
- 旧智能体能力清单到新 runtime 的映射表。
- Tool/action migration matrix。
- Eval suite backlog。

建议 PR：

1. `docs: add agent runtime continuation migration inventory`
2. `test: add agent migration acceptance placeholders`

验收：

- 列出旧 agent 的所有入口、skill、tool、业务流程、stream event 和写操作。
- 每项标记为 `done`、`partial`、`missing`、`removed`。
- 每个 `partial/missing` 都有目标模块、测试类型和优先级。

## Phase 1: Real LangGraph Orchestration

目标：把当前 graph registry 升级为真实可执行、可 checkpoint、可 resume 的
LangGraph workflow。

范围：

- 定义 `AgentGraphState` 的最小稳定 schema。
- 实现 `StateGraph` 节点：
  - `load_context`
  - `safety_gate`
  - `sdk_reasoning`
  - `tool_result_review`
  - `action_policy`
  - `confirmation_interrupt`
  - `final_response`
  - `finish`
- 接入 Postgres checkpoint store 或明确的 checkpoint adapter。
- 把 waiting/interrupt 状态和 `agent_runs.status` 对齐。
- 确认 cancel、timeout、retry、resume 的语义。

建议 PR：

1. `agent: introduce executable LangGraph graph`
2. `agent: persist and restore graph checkpoints`
3. `agent: add graph resume and interrupt tests`

验收：

- run worker 调用真实 graph，而不是只调用线性 executor。
- 进程重启后，waiting run 能从 checkpoint 恢复。
- graph state 不包含业务权威事实。
- graph tests 覆盖 completed、failed、cancelled、waiting_for_confirmation、
  resume。

## Phase 2: OpenAI Agents SDK Productization

目标：把 SDK runner 从 adapter 边界推进成可运维的产品级 SDK node。

范围：

- 用环境变量控制 model、timeout、max turns、trace flag、prompt version。
- 定义 MomCozy base agent instructions 和 specialist agent 选择规则。
- 将 tool contracts 映射为 SDK tools。
- 捕获 SDK tool calls、guardrail decisions、token/cost/latency metrics。
- 明确 SDK failure mapping：dependency missing、rate limit、model error、
  tool schema rejection、empty output。
- 增加 provider mocking harness，避免 CI 依赖真实模型。

建议 PR：

1. `agent: harden OpenAI Agents SDK runner settings` - done
2. `agent: add SDK tool and tracing contract tests` - done
3. `agent: add deterministic SDK mock harness for evals` - done
4. `agent: add deterministic specialist routing` - done

已完成能力：

- `OPENAI_MODEL`、`OPENAI_AGENT_MAX_TURNS`、`OPENAI_AGENT_TIMEOUT_SECONDS`、
  `OPENAI_AGENT_TRACE_ENABLED`、`OPENAI_AGENT_PROMPT_VERSION` 均由 typed
  settings 和 `.env.example` 管控。
- `/v1/agent/runs` 在客户端未显式传入 `prompt_version` 时使用环境默认
  prompt version。
- SDK request、context projection 和 OpenAI Agents SDK `RunConfig` 均带
  prompt version / trace metadata。
- 默认关闭 provider tracing；开启 tracing 时仍不使用 `previous_response_id`、
  conversation id 或 SDK session 作为续聊状态。
- SDK tool 调用 contract tests 覆盖 tool schema 映射、safe output、应用侧
  `ToolExecutor` 路径，以及 provider tracing metadata。
- `ScriptedSdkBackend` 可在 CI/eval 中按脚本触发 SDK tool invocation，
  不依赖真实模型也能验证完整 run lifecycle。
- 每个 run 都会选择一个 deterministic specialist profile；profile 只影响
  SDK instructions、context/trace metadata 和可见 tool allowlist，不创建旧
  adapter、不依赖 provider session state，也不绕过应用侧 tool executor。

验收：

- 没有 `OPENAI_API_KEY` 时，worker 在启用状态下 fail fast。
- SDK node 所有失败都有稳定 error code。
- SDK tool 调用仍经过 backend permission、schema、audit 和 safe payload。
- CI 可用 fake SDK backend 跑完整 run lifecycle。

## Phase 3: Business Context And Read Tools

目标：先迁移只读能力，让模型能基于真实业务事实回答，而不是依赖旧
`ContextState`。

优先级：

1. profile / infant context。
2. records：feeding、pumping、growth、milk trends。
3. plans / tasks。
4. pregnancy diary。
5. devices / telemetry。
6. files / vision summaries。

建议 tool：

- `profile.read`
- `business.context.read`
- `records.milk_summary.read`
- `plans.current.read`
- `diary.recent.read`
- `devices.pump_status.read`
- `files.vision_summary.read`

建议 PR：

1. `agent: add milk summary read tool` - done
2. `agent: add plan and diary context read tools` - done
3. `agent: add device and file context read tools` - done

已完成能力：

- `records.milk_summary.read` 已返回 owner-scoped feeding、pumping、trend 和
  bounded infant projection，支持“回答前先读真实奶量事实和宝宝资料”。
- `plans.current.read`、`diary.recent.read`、`devices.pump_status.read`、
  `devices.guidance_assets.read`、`files.vision_summary.read` 已纳入 tool
  registry、schema、default handler wiring 和 handler tests。

验收：

- 每个 read tool 有 JSON Schema、permission、owner scope、timeout 和安全输出。
- 工具结果只返回模型需要的 bounded summary，不返回原始大对象。
- cross-user fixture 测试必须 fail closed。
- eval 覆盖“回答前应先读业务事实”的主流程。

## Phase 4: First Complete Vertical Flow - Milk Management

目标：选择奶量管理作为第一个完整垂直流程，跑通从用户体验到后端 action、
event、eval 的全链路。

范围：

- 当前奶量事实读取：喂养记录、吸奶记录、趋势摘要、宝宝资料。
- 奶量解释和日总结。
- 新增/更新记录的 action proposal。
- 奶量计划或提醒的 action proposal。
- 用户确认后通过 outbox apply 到 records/plans/notifications。
- stream events 让 Flutter reducer 可稳定合并状态。

建议 tool/action：

- `records.milk_summary.read`
- `records.feeding_record.propose`
- `records.pumping_record.propose`
- `plans.milk_plan.propose`
- `notifications.milk_reminder.propose`

建议 PR：

1. `agent: add milk management eval fixtures` - done
2. `agent: add milk read tools` - done
3. `agent: add feeding and pumping action proposals` - done
4. `agent: add milk action outbox apply handlers` - done
5. `agent: add milk management end-to-end tests` - done

已完成能力：

- 奶量日报、奶量趋势、奶量计划、提醒创建 eval seed 已对齐当前 tool
  contracts。
- `records.feeding_record.propose`、`records.pumping_record.propose`、
  `plans.milk_plan.propose`、`notifications.milk_reminder.propose` 均为
  confirmation-first action proposal。
- feeding / pumping / milk plan / milk reminder confirmed actions 均通过
  outbox apply handler 落到业务 service。
- 奶量 feeding 主流程测试覆盖 run 创建、action confirmation、outbox apply、
  重复确认幂等和 replayable application events。

验收：

- 用户问“今天奶量怎么样”时，必须读取 records 后回答。
- 用户要求新增记录时，先生成 confirmation_required action。
- 确认后 action 进入 outbox，并最终 applied。
- 重复确认不会重复写记录。
- SSE replay 能完整重放 run.started、tool events、action events、
  message.completed/run.completed 或 run.waiting_for_confirmation。
- follow mode 可发送 Redis transient `message.delta`，但断线恢复不能依赖
  delta 完整存在；最终内容以 persisted `message.completed` 和 message
  ledger 为准。
- eval 覆盖：只读总结、创建记录、修改记录、重复确认、越权、红旗安全。

## Phase 5: Pregnancy Plan And Diary Flow

目标：迁移孕期计划和孕期日记主流程。

范围：

- 当前孕周、预产期、计划、任务、日记、健康 note 的读取。
- 孕期计划生成 proposal。
- 任务创建/完成 proposal。
- 日记写入 proposal。
- 健康红旗下阻断普通流程并给出安全建议。

建议 tool/action：

- `pregnancy.plan_context.read`
- `pregnancy.plan_create.propose`
- `plans.task_create.propose`
- `plans.task_complete.propose`
- `diary.entry_upsert.propose`

建议 PR：

1. `agent: add pregnancy plan context tools` - done
2. `agent: add pregnancy plan action proposals` - done
3. `agent: add diary action proposals` - done
4. `agent: add pregnancy plan and diary evals` - done

已完成能力：

- `pregnancy.plan_context.read` 已聚合 owner-scoped profile、active plans、
  tasks 和 recent diary entries。
- 孕期计划生成和任务完成 eval seed 已要求先读
  `pregnancy.plan_context.read`，再进入 action proposal。
- `pregnancy.plan_create.propose`、`plans.task_create.propose`、
  `plans.task_complete.propose`、`diary.entry_upsert.propose` 均为
  confirmation-first action proposal。
- 孕期计划 confirmed action 已有 outbox apply 主流程测试；diary upsert
  已有 action handler 和 soft-delete restore 体验测试。

验收：

- 不再把孕期流程状态塞进 session state。
- 计划/日记/任务都从业务表读取和写入。
- 高风险健康内容触发 deterministic safety gate。
- eval 覆盖计划生成、任务更新、日记写入、健康红旗、取消/恢复。

## Phase 6: Hospital Bag, Device Guidance, And Support

目标：完成剩余业务域的 agent 化，优先保留对用户最有价值且风险可控的流程。

范围：

- 待产包 cart update 从当前 partial 状态完善为完整主流程。
- 设备指导只读诊断、使用建议、故障排查。
- 支持工单已具备 action apply handler，继续补齐体验和 eval。
- 高风险设备/售后场景转人工或创建 support ticket。

建议 PR：

1. `agent: complete hospital bag action lifecycle` - done
2. `agent: add device guidance read tools and evals` - done
3. `agent: harden support ticket handoff flow` - done

当前进展：

- 待产包 cart update 已具备 action proposal、confirmation、outbox apply、event replay 测试，并在 eval seed 中新增 `hospital_bag_cart_update`。
- 设备指导保持 read-only：`devices.pump_status.read` 读取 owner-scoped 设备状态，`devices.guidance_assets.read` 读取受控资料元数据；eval seed 覆盖未知型号先澄清和已知设备先读状态/资料两类体验。
- 支持工单已通过 `support.ticket.propose` 进入确认流，由 outbox apply handler 调用 support service 创建工单；高风险设备售后 handoff 和 IBCLC handoff 均使用当前 action contract。

验收：

- 设备指导不会直接修改设备状态，除非有明确 action contract。已覆盖。
- 支持工单 action 不泄漏 `apply_payload` 给前端。已覆盖。
- 待产包和支持流均有 replay/eval case。已覆盖。

## Phase 7: Health, Emotion, And Safety Hardening

目标：把高风险母婴健康和情绪场景从 prompt-only 升级为 deterministic gate +
eval regression。

范围：

- 完善 red-flag classifier/rules。
- 区分 block、escalate、allow。
- 高风险场景禁用普通业务写流程。
- 安全回复模板版本化。
- 人工/专业支持 handoff 事件持久化。

建议 PR：

1. `agent: expand maternal and infant health red flags` - done
2. `agent: add emotional crisis escalation flow` - done
3. `agent: add safety eval regression suite` - done

当前进展：

- Deterministic safety guard 已覆盖母婴健康红旗、婴儿高风险症状、情绪/自伤/可能伤害宝宝、prompt injection，并输出 `allow`、`escalate`、`block`。
- 高风险输入会在 run 创建阶段写入 `agent_safety_events`，发送 `safety.blocked`，并阻断普通 agent run，不会进入工具/action 流程。
- `safety.blocked` event 已带 `response_template_key`、`response_template_version`、`handoff_type`，便于 App 端展示版本化安全引导。
- Eval seed 已包含 health、infant health、emotion、harm-baby、mixed intent、permission bypass、prompt injection regression；runner 使用 deterministic assertions 验证 safety decision 和 forbidden side effect。

验收：

- critical safety suite 100% pass。已覆盖本地 seed runner。
- permission bypass suite 100% pass。已覆盖 seed case。
- 高风险输入不会创建普通业务 action。已覆盖。
- safety events 可被 replay bundle 导出，且不在 metrics 中暴露 PII。已覆盖。

后续产品化：

- 安全回复模板的具体文案仍需由产品/合规确认后在 App 端或配置层落地。
- 人工/专业支持 handoff 的真实运营闭环仍需结合客服/IBCLC 流程接入。

## Phase 8: Long-Term Memory

目标：只为有明确产品价值的信息引入长期记忆，不把 memory 当业务数据库。

范围：

- 定义 memory 类型：
  - user preference
  - stable care preference
  - communication preference
  - recurring constraint
- 定义 memory write policy：
  - 模型只能 propose memory。
  - 后端校验 schema、来源、置信度、敏感类别。
  - 必要时需要用户确认。
- 定义 memory retrieval：
  - 每轮只取少量 relevant memory projection。
  - memory 必须可解释、可删除、可过期。

建议 PR：

1. `agent: add memory schema and repository` - done
2. `agent: add memory proposal action` - done
3. `agent: add memory retrieval projection tests` - done

后续产品化 PR：

1. `agent: add memory sensitivity and retention policy` - done
2. `eval: add memory write and retrieval regression suite` - done
3. `app: add user-visible memory management UX` - deferred until Flutter integration

验收：

- memory 不替代 profile、records、plans、diary 等业务表。
- backend 已保证模型只能 propose memory，确认后通过 outbox 写入。
- 每轮只投射少量 active memory，且 memory projection 独立于 business facts。
- backend 已支持用户查看和删除/归档 active memory。
- backend 已拒绝 health、child、crisis、regulated 等敏感 memory 写入，并通过 `memory_sensitive_rejection` eval 固化。
- backend 已支持 `expires_in_days`，active memory projection 不会返回过期记忆。
- backend 已支持 `GET/PUT /v1/agent/memories/settings` 禁用 memory；禁用后不再写入或投射 memory，仍可查看/删除已有 memory。
- 仍需补齐 Flutter 端用户可见 memory 管理 UX。
- prompt cache 稳定片段不因 memory 大量变化而失效。

## Phase 9: Eval Harness And Acceptance Loop

目标：让 Codex 和团队可以持续工作直到达到验收标准。

测试层级：

- unit：context builder、tool/action policy、safe payload、event encoding。
- integration：run creation、worker execution、tool call、action confirm、
  outbox apply、cancel、replay。
- graph：node transition、checkpoint resume、interrupt、retry、timeout。
- contract：OpenAPI、event schema、action schema、tool schema。
- eval：业务主流程、工具选择、安全、权限、stream/replay、最终回复质量。

每个 eval case 至少包含：

- `suite`
- `name`
- `input.thread_history`
- `input.user_message`
- `fixtures.business_state`
- `expected.required_tools`
- `expected.forbidden_tools`
- `expected.action`
- `expected.safety_decision`
- `expected.stream_events`
- `expected.final_response_rubric`

建议 PR：

1. `eval: expand product agent seed suites` - done
2. `eval: add agent eval runner` - done
3. `ci: add critical agent eval gates` - done

已完成能力：

- product seed schema 和 required suite 校验。
- seed suites 已覆盖奶量、孕期计划、日记、设备、支持、健康、情绪、安全、越权、prompt injection、memory preference、sensitive memory rejection。
- deterministic assertion engine：required tool、safety decision、
  forbidden tool、confirmation-required、forbidden side effect。
- seed eval runner 和 CLI，可对 product seed 做 deterministic smoke，并可接收 observed trace fixtures。
- seed eval runner 可输出 JSON summary 和 JUnit XML report，便于 CI/发布门禁归档。
- isolated runtime client 已可执行 mocked SDK run、从 runtime ledger 收集
  messages/events/tool calls/actions/safety trace，并直接运行 seed assertions。
- replay bundle eval runner 和 CLI，可对 redacted replay JSON 输出 pass/fail
  报告。
- CI 已接入 `run_agent_seed_eval.py` smoke gate。
- CI 已上传 seed eval JSON/JUnit 报告 artifact，便于失败排查。
- `run_agent_provider_eval.py` 已提供 provider-backed eval harness：无凭证时可
  明确 skipped，有凭证时复用 OpenAI Agents SDK runner、当前 specialist
  routing、tool schema 和 seed assertion engine 生成 JSON 报告。
- `.github/workflows/agent-provider-eval.yml` 已支持手动/定时运行，并上传
  `agent-provider-eval` 报告 artifact。

后续 PR：

1. `eval: add isolated runtime client for agent seed cases` - done
2. `eval: add provider-backed nightly evals` - done
3. `eval: add report artifact upload in CI` - done

验收：

- 奶量、孕期计划、日记、设备、支持、健康、情绪、安全、越权、prompt
  injection 都有最小 eval set。
- critical safety、permission bypass、stream contract 必须 100% pass。
- prompt/tool/graph 改动自动触发相关 eval。

## Phase 10: Flutter Integration

目标：后端 agent contract 稳定后，再和 Flutter 新端打通。

范围：

- 生成或校验 typed client。
- 登录后用 Bearer header 调用 agent API。
- 创建 thread/run。
- 用 `/v1/agent/runs/{run_id}/stream?after_sequence=...&follow=true`
  消费 SSE。
- reducer 按 `event_id`、`thread_id`、`run_id`、`sequence`、`type`、
  `action_id`、`message_id` 合并状态；`message.delta` 是 provisional UI
  事件，可能没有 `sequence`，最终由 `message.completed` 覆盖。
- action card 支持 confirm/reject。
- 网络断开后用 `after_sequence` replay。
- App 侧保存按用户隔离的最小 Agent Hub 快照，用于
  `waiting_for_confirmation`、断流和 app 冷启动恢复；快照不是业务事实
  来源，后端 run/message/action ledger 仍是权威。
- action confirm/reject 后继续 follow 原 run stream，让后端终态事件覆盖
  本地 pending/queued 状态。

建议 PR：

1. `docs: add Flutter agent integration smoke flows` - done
2. `test: add backend contract tests for Flutter reducer events` - done
3. `app: integrate agent stream and action confirmation` - in progress

验收：

- token 不出现在 URL。
- 断线重连不重复展示 message/action。
- waiting_for_confirmation 可以跨 app 重启恢复。
- Flutter 不解析自然语言来判断状态。

## Completion Criteria

只有同时满足以下条件，才能认为新后端智能体重构完成：

- 所有旧核心业务主流程都有新 runtime 下的 owner-scoped tool/action/eval。
- 旧 Responses API loop、`previous_response_id`、`ChatSession`、AG-UI bridge
  依赖不再出现在新后端路径。
- LangGraph checkpoint、SDK runner、tool executor、action/outbox、event stream、
  replay/eval 均由测试覆盖。
- 每个写操作都有 permission、idempotency、audit 和失败语义。
- 每个高风险健康/情绪场景都有 deterministic guard 和 eval regression。
- Flutter 新端完成 typed API、SSE replay、action confirm/reject、断线恢复。
- CI 至少覆盖 unit、integration、contract、migration、worker、critical eval。
- release smoke checklist 能在本地 Docker/compose 和 staging 环境通过。

## Recommended Immediate Next PR

后端-only 的下一步建议：

```text
PR: ops: configure provider-backed eval credentials and budget
```

包含：

- 配置 GitHub secret `OPENAI_API_KEY` 和可选 repo var `OPENAI_MODEL`。
- 确定 nightly/manual eval 的 suite 范围、`max_cases`、成本预算和失败阈值。
- 第一次真实运行后，把 provider report 中的 flaky case 标记为 quarantine
  或转成更具体的 deterministic regression。
- 根据真实 token/latency 数据补充 SLO 和告警阈值。

该 PR 依赖真实 provider credential、成本策略和运行环境，不属于当前本地代码
分支可以完全闭环的工作。

Flutter integration 已在当前分支启动。剩余收口重点是 typed client/OpenAPI
校验、真实 Flutter SDK 环境下的 widget/integration test、以及 staging smoke。
