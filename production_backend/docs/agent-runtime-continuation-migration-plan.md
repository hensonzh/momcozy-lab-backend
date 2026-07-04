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
  `load_context -> safety_gate -> sdk_reasoning -> tool_result_review ->
  action_policy -> confirmation_interrupt/final_response -> finish`。

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

仍未完成：

- OpenAI Agents SDK 已有 adapter 边界，但业务 specialist agents、handoff、
  guardrail、tracing 策略还没有完整产品化。
- 旧智能体中的主要业务能力还没有完整迁移成新 tool/action contract。
- 待产包、设备指导、IBCLC、健康/情绪安全、图片/语音
  等主流程尚未达到新架构下的完整 parity；设备指导仍缺完整问答策略和
  更多 eval，IBCLC 仍缺独立专业支持模块和更细的 handoff policy。
- 长期记忆还缺禁用路径、敏感记忆策略深化、Flutter 端可视化体验和
  eval 回归。
- eval 样例和自动化行为评估已有基础，但还缺真实 runtime client、
  CI gate、报告归档和更多业务主流程覆盖。
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
- Redis 只保存 active run lock、cancel flag、stream cursor、短期 cache。
- Context projection 每轮由 message ledger、business facts、memory 和当前
  run state 派生，不作为权威状态持久化。

### Tools And Actions

- 每个 tool 都有正式 contract：name、schema、permission、owner scope、
  side effect level、blocking policy、timeout、safe args/result、audit。
- read tool 可直接返回安全摘要。
- write tool 默认只生成 action proposal。
- 中高风险 action 必须走 `preview -> confirmation -> apply -> audit`。
- 不影响下一步推理的副作用进入 outbox effect lane。

### Streaming

- 前端只消费 application event，不消费 provider raw event。
- SSE replay 和 follow mode 均基于 persisted event + sequence cursor。
- reconnect 后客户端用 `after_sequence` 继续拉取，不依赖内存 stream。

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
2. `agent: add SDK tool and tracing contract tests`
3. `agent: add deterministic SDK mock harness for evals`

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

1. `agent: add milk summary read tool`
2. `agent: add plan and diary context read tools`
3. `agent: add device and file context read tools`

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

1. `agent: add milk management eval fixtures`
2. `agent: add milk read tools`
3. `agent: add feeding and pumping action proposals`
4. `agent: add milk action outbox apply handlers`
5. `agent: add milk management end-to-end tests`

验收：

- 用户问“今天奶量怎么样”时，必须读取 records 后回答。
- 用户要求新增记录时，先生成 confirmation_required action。
- 确认后 action 进入 outbox，并最终 applied。
- 重复确认不会重复写记录。
- SSE replay 能完整重放 run.started、tool events、action events、
  message.completed/run.completed 或 run.waiting_for_confirmation。
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

1. `agent: add pregnancy plan context tools`
2. `agent: add pregnancy plan action proposals`
3. `agent: add diary action proposals`
4. `agent: add pregnancy plan and diary evals`

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

1. `agent: complete hospital bag action lifecycle`
2. `agent: add device guidance read tools and evals` - partial
3. `agent: harden support ticket handoff flow`

验收：

- 设备指导不会直接修改设备状态，除非有明确 action contract。
- 支持工单 action 不泄漏 `apply_payload` 给前端。
- 待产包和支持流均有 replay/eval case。

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

1. `agent: expand maternal and infant health red flags`
2. `agent: add emotional crisis escalation flow`
3. `agent: add safety eval regression suite`

验收：

- critical safety suite 100% pass。
- permission bypass suite 100% pass。
- 高风险输入不会创建普通业务 action。
- safety events 可被 replay bundle 导出，且不在 metrics 中暴露 PII。

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

1. `agent: add memory sensitivity and retention policy`
2. `eval: add memory write and retrieval regression suite`
3. `app: add user-visible memory management UX`

验收：

- memory 不替代 profile、records、plans、diary 等业务表。
- backend 已保证模型只能 propose memory，确认后通过 outbox 写入。
- 每轮只投射少量 active memory，且 memory projection 独立于 business facts。
- backend 已支持用户查看和删除/归档 active memory。
- 仍需补齐用户禁用 memory 的产品 API/UX。
- 仍需补齐敏感健康事实不被静默写入长期记忆的 eval 和产品策略。
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

1. `eval: expand product agent seed suites` - partial
2. `eval: add agent eval runner` - partial
3. `ci: add critical agent eval gates`

已完成能力：

- product seed schema 和 required suite 校验。
- memory preference capture seed case。
- deterministic assertion engine：required tool、safety decision、
  confirmation-required。
- replay bundle eval runner 和 CLI，可对 redacted replay JSON 输出 pass/fail
  报告。

后续 PR：

1. `eval: add isolated runtime client for agent seed cases`
2. `eval: expand device support and health safety regression cases`
3. `ci: add critical agent eval smoke gate`

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
  `action_id`、`message_id` 合并状态。
- action card 支持 confirm/reject。
- 网络断开后用 `after_sequence` replay。

建议 PR：

1. `docs: add Flutter agent integration smoke flows`
2. `test: add backend contract tests for Flutter reducer events`
3. `app: integrate agent stream and action confirmation`

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

下一步建议从 Phase 1 开始：

```text
PR: agent: introduce executable LangGraph graph
```

包含：

- 新增真实 LangGraph graph factory。
- 将当前 `AgentRuntimeExecutor` 包装为 `sdk_reasoning` node 或拆成节点函数。
- 接入 checkpoint store。
- 保留现有 API/event/action contract 不变。
- 增加 graph completed / waiting / resume / cancel 测试。

完成后，再进入 Phase 4 的第一个完整垂直业务流程：奶量管理。
