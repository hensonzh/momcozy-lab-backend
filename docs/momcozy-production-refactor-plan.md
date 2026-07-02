# MomCozy 生产级重构总览

本文档合并并取代以下四份临时文档：

- `legacy-momcozy-agent-architecture.md`
- `momcozy-product-refactor-notes.md`
- `production-readiness-review.md`
- `production-refactor-target-architecture.md`

它的作用是用一份项目级文档讲清楚：

1. 当前旧系统是什么。
2. 当前离生产级产品还缺什么。
3. 目标生产级架构应该是什么。
4. 如何从当前 demo / 用户测试形态迁移到生产级产品。

本文档的目标架构基于三个全局 architecture skill 的原则总结：

- `flutter-product-app-architecture`：Flutter App 的 feature-first、typed API client、secure storage、Agent 流式 UI、测试和发布体系。
- `production-fastapi-backend-architecture`：FastAPI 后端的 app factory、router/service/repository 分层、PostgreSQL/Alembic、认证授权、API 契约、部署和运维。
- `production-agent-architecture`：Agent runtime 的 run ledger、tool/action/audit、state/persistence、stream/replay、guardrail、eval 和可恢复性。

## 总体结论

当前 MomCozyAgent 是一个产品探索型 Agent 后端，适合内部 demo、产品验证和用户测试，不适合直接面向真实用户生产上线。

它已经具备有价值的雏形：

- Responses API agent loop 已经跑通。
- service skill manifest 和渐进式 `load_skill` 方向正确。
- AG-UI / WebSocket / SSE 流式体验已有基础。
- tool registry、tool schemas、safe tool payload 已经有初步边界。
- 母婴、哺乳、孕期、设备、日记、图片和语音等业务场景已有探索。

但生产级缺口主要不在模型能力，而在工程系统：

```text
认证授权
-> 数据持久化
-> Agent runtime ledger
-> Action / Audit / Outbox
-> 安全 guardrails
-> 可观测与 eval
-> Flutter 原生产品工程
-> CI/CD 与运维
```

上线真实用户前，优先级应是：

```text
认证授权 -> PostgreSQL/Redis/OSS -> agent run/action 审计 -> 安全 guardrails -> 可观测与 eval -> Flutter App 迁移
```

## 阅读层级约定

为避免把不同层级的问题混在一起，本文按以下层级组织：

- **现状**：描述旧系统现在如何工作，只陈述事实，不代表目标方案。
- **缺陷**：说明当前实现为什么不能生产上线，按 P0 / P1 / P2 标记风险优先级。
- **目标架构**：定义未来系统的稳定边界、数据归属、运行时契约和部署方式。
- **迁移计划**：描述从旧系统到目标架构的阶段性落地顺序。
- **验收标准**：给第三方团队、QA、支持和安全审查使用的完成判据。

概念层级上，本文区分：

- **业务能力**：用户、宝宝资料、奶量记录、孕期计划、日记、文件、设备等产品事实。
- **应用分层**：Flutter App、FastAPI API、service/use case、repository、infrastructure client、worker。
- **Agent runtime**：thread、run、message、tool call、artifact、action、event、workflow state。
- **状态存储**：业务表、runtime ledger、workflow checkpoint、Redis transient controls、long-term memory。
- **派生上下文**：每轮模型调用的 context projection，它由状态源（含业务事实）计算出来，不是权威状态。
- **迁移动作**：迁移阶段里的任务和验收，不应被理解为长期架构组件。

## 当前旧系统是什么

当前系统主要由两个工程组成：

```text
MomCozyApp
  Vite + React + Capacitor
  页面、状态、Agent streaming、语音、设备、记录、本地逻辑

MomCozyAgent
  Python + FastAPI 入口
  OpenAI Responses API agent loop
  SQLite + 本地文件 + 内存 ChatSession
  ENTRY_API_KEY 静态 Bearer token
```

### 当前前端

`MomCozyApp` 是 React / Vite / Capacitor App。它目前提供了大量产品行为参考：

- AgentHub、Records、Schedule、Status、PumpSession、DeviceManagement、IbclcChat 等页面。
- Agent streaming、work progress、artifact 展示、voice、BLE、通知、本地持久化等逻辑。
- API 主要集中在 `src/lib/agentApi.ts`、`src/lib/http.ts`。
- 身份和环境主要依赖 `VITE_API_BASE_URL`、`VITE_API_TOKEN`、debug/local user id。

这些行为可作为 Flutter 重构时的功能验收参考，但当前前端不是目标生产架构。

### 当前后端

`MomCozyAgent` 当前后端形态：

- `api_app.py` 提供 FastAPI app 入口，但整体仍偏单体 router。
- `/api/ag-ui`、`/api/ag-ui-ws`、`/api/ag-ui-prewarm` 承载 agent 对话与预热。
- `server.py` / `ChatRuntime.sessions` 用内存保存 `ChatSession`。
- `data_store.py` 使用 SQLite 并在代码内执行建表和迁移。
- 文件上传保存到本地 `upload_files`。
- 鉴权主要是 `ENTRY_API_KEY` 静态 Bearer token，不是用户级认证。

### 当前 Agent loop

当前 Agent 直接使用 OpenAI Responses API，不使用 OpenAI Agents SDK，也不在模型调用前做应用侧服务预路由。

主链路：

```text
Web / App
  -> POST /api/ag-ui 或 WS /api/ag-ui-ws
  -> server.py / bridge 解析 AG-UI payload
  -> ChatSession 恢复 previous_response_id、loaded_skill_ids、ContextState
  -> agents.py run_agent_loop()
  -> build_agent_request()
  -> client.responses.create()
  -> 模型输出 text 或 function_call
  -> 应用执行 tool handler
  -> function_call_output 回传 Responses API
  -> text delta / tool event / artifact event 经 SSE 或 WebSocket 流给前端
```

关键文件：

- `src/momcozy_agent/agents.py`：Responses API 请求构造、tool loop、流式事件、工具结果压缩。
- `src/momcozy_agent/server.py`：HTTP/SSE 服务、AG-UI payload 转换、内存会话。
- `src/momcozy_agent/api/chat_ws_bridge.py`：App WebSocket 到内部 SSE agent stream 的桥接。
- `src/momcozy_agent/static_context.py`：稳定 agent 指令、安全策略、service skill manifest。
- `src/momcozy_agent/contexts.py`：`request_context` 与 `ContextState`。
- `src/momcozy_agent/skills.py`：service skill registry、manifest 解析、`load_skill`。
- `src/momcozy_agent/tool_registry.py`：工具暴露和 handler dispatch。
- `src/momcozy_agent/tool_schemas.py`：Responses API function schemas。

### 当前 service skill

旧 service skill ids：

- `birth-prep`
- `milk-management`
- `health-consultation`
- `emotion-support`
- `device-guidance`

旧架构的合理点是：不一次性加载所有 skill 全文，而是通过 manifest + `load_skill` 渐进式加载，这符合上下文控制和 prompt cache 思路。

### 当前上下文和状态

`ContextState` 同时承载多种状态：

- loaded references / loaded tools / client events。
- profile slots / birth prep slots / milk management state。
- last assistant message。
- device image state。
- 当前服务域、图片上下文、工具图片等。

这对 demo 有帮助，但生产级需要拆分。否则它会同时像 prompt context、workflow state 和业务缓存，难以恢复、审计和测试。

## 当前主要缺陷

本节按风险优先级描述缺陷；P0 / P1 / P2 表示处理优先级，不表示系统层级。阅读顺序上，先看跨系统上线风险，再看后端数据与 API 缺陷，再看 Agent runtime 内部工程分层缺陷，最后看部署和 CI/CD 基础缺口。

### P0：用户体系和权限边界缺失

当前主要依赖 `ENTRY_API_KEY` 静态 Bearer token。业务接口普遍信任请求中的 `user_id`。

风险：

- 任意持有 token 的客户端可以伪造其他用户。
- 没有 access token、refresh token、device session、RBAC / ABAC。
- WebSocket query token 容易泄漏。
- 文件、日记、计划、喂养记录、设备数据没有统一 owner scope。
- `uploaded_file` 这类文件 metadata 缺少完整 `owner_user_id` 校验链路。

生产要求：

- 所有用户作用域来自后端 `current_user`。
- body/query 中的 `user_id` 只能作为被授权过滤条件，不能作为权限来源。
- 静态 API key 仅保留为内部 service key。
- WebSocket 使用 access token header；如平台限制 header，则使用后端签发的短期 WS token。

### P0/P1：Agent session 是内存态，无法多实例和恢复

当前 `ChatRuntime.sessions` 是进程内 dict，保存：

- `previous_response_id`
- `loaded_skill_ids`
- `ContextState`
- active run / cancel state

问题：

- 服务重启后多轮对话断掉。
- 多 worker / 多 pod 下，同一 thread 路由到不同实例会丢上下文。
- cancel、run lock、active_run_id 只在本进程有效。
- 断流或崩溃后无法恢复 run、tool call、artifact、action。

生产要求：

- PostgreSQL 保存 thread、run、message、tool call、artifact、action、checkpoint。
- Redis 保存 active run lock、cancel flag、stream cursor、短期缓存。
- 每轮工具调用和副作用必须可追踪、可重放、可审计。

### P1：数据层仍是 SQLite + 裸 SQL + 代码内迁移

`data_store.py` 体量大，SQLite 表结构、查询、迁移和业务逻辑混在一起。

问题：

- 没有 Alembic migration。
- 没有清晰事务边界。
- 没有 repository/service/use-case 分层。
- SQLite 不适合生产并发写和多实例部署。
- audit log、actor_user_id、request_id、idempotency_key 覆盖不足。

生产要求：

- PostgreSQL 作为权威数据源。
- SQLAlchemy 2.0 + Alembic 管理 schema。
- repository 封装 DB 访问。
- service/use case 负责权限、事务、幂等、业务规则。

### P1：写操作缺少统一 Action / Approval / Audit 体系

当前写操作不一致：

- 有些工具有确认字段。
- 有些依赖 prompt 约束。
- 有些直接写库。
- profile 写入存在内存异步队列。

生产级 Agent 不能只靠模型理解是否需要确认。所有中高风险写操作应统一为：

```text
preview -> confirmation -> apply -> audit log
```

每个 action 至少需要：

- `actor_user_id`
- target resource
- permission check
- `idempotency_key`
- `request_id`
- before / after snapshot
- rollback / compensation 策略

不影响下一步模型推理的写入、更新、删除，应进入 durable outbox，由 worker 异步 apply，并通过 `action.queued` / `action.applied` / `action.failed` 事件反馈。

### P1：母婴健康和情绪安全主要靠 prompt

MomCozy 涉及母婴、哺乳、宝宝、心理危机场景，不能只靠 prompt。

生产必须具备应用侧硬护栏：

- deterministic red-flag classifier / rule gate。
- 医疗和情绪危机升级路径。
- 高风险场景禁用普通业务流程。
- 人工或专业支持 handoff。
- 医疗建议输出审查策略。
- 安全事件记录。
- 安全 eval regression：红旗、情绪危机、权限越权、工具误用、action confirmation。

prompt 与 agent instructions 只能作为辅助说明；安全、权限和副作用必须由应用侧强制执行。

### P1：可观测性不足

当前主要是本地 JSONL timing log，且部分错误会把 `str(exc)` 流给前端。

缺少：

- request_id / trace_id / thread_id / run_id 全链路关联。
- structured logs。
- metrics：延迟、token、工具成功率、错误率、取消率。
- tracing：model call、tool call、DB、Redis、OSS、外部 API。
- alerting / SLO。
- support / QA / incident 所需 run replay。

生产要求：错误对前端使用稳定 error code，内部细节只进入受保护日志和 trace。

### P1/P2：API 契约不稳定

当前 router 混合了参数校验、鉴权、DB 调用、业务规则、外部模型调用和响应拼装。

问题：

- Pydantic schema / OpenAPI 不完整。
- 错误结构不统一。
- 分页、过滤、幂等、副作用声明不统一。
- 前端容易依赖自然语言错误或临时字段。

生产要求：

```text
request schema
response schema
error schema
auth / permission
owner scope
idempotency
pagination / filtering
side effects
audit requirement
```

### P1：Agent loop 过于巨大，核心职责集中在 `agents.py`

当前 `agents.py` 承担过多职责，包括：

- Responses API request 构造。
- tool loop 和 function call output 回填。
- stream delta 读取与事件映射。
- tool result 压缩和安全 payload 处理。
- 图片上下文和视觉输入处理。
- quick replies、web search citation、业务特殊规则。
- run cancel、线程/reader 管理和异常处理。

这会造成：

- request builder、tool executor、stream adapter、event mapper 无法独立测试。
- 业务特殊规则和底层 runtime 逻辑互相缠绕，新增服务场景容易回归旧路径。
- 多人协作时频繁改同一个超大文件，冲突和回归概率高。
- 后续迁移到 `AgentRuntimeService`、LangGraph 或 SDK adapter 时缺少清晰切分点。

生产级应拆成：

```text
request builder
context projector
model / SDK adapter
tool executor
action manager
stream adapter
event mapper
safety guard
workflow policy
runtime stores
```

### P1：`ContextState` 混合上下文、运行态和业务事实

当前 `ContextState` 同时保存：

- loaded references / loaded tools / client events。
- profile slots、birth prep slots、milk management state。
- last assistant message。
- device image state、当前设备/服务域、工具图片等。

这会让一个对象同时承担 prompt context、workflow state、业务缓存和 UI 辅助状态，导致：

- 难以判断哪些状态必须持久化，哪些状态可丢弃。
- 难以跨进程或服务重启恢复。
- 业务事实可能被藏在 prompt/runtime state 中，而不是权威业务表。
- 模型上下文投影不可重建，影响 replay、eval 和事故排查。

生产级应拆成两个层级：第一层是系统保存和恢复的状态源，第二层是每轮模型调用临时派生出的输入视图。

```text
State stores / sources
  Business state
    产品权威事实，存在业务表，由领域 service/repository 读写。
    例如用户、宝宝资料、奶量记录、奶量计划、孕期计划、日记、文件 metadata、设备数据。

  Runtime ledger
    Agent 对话容器和执行账本，存在 agent_threads、agent_runs、agent_messages、
    agent_tool_calls、agent_tool_outputs、agent_artifacts、agent_actions、
    agent_events、agent_audit_logs。

  Workflow state / checkpoint
    为了恢复 agent / workflow 执行而保存的运行状态，存在 agent_workflow_states、
    agent_context_checkpoints 或 LangGraph checkpointer 中。
    例如当前 workflow 节点、等待用户确认的 interrupt、下一步要恢复的 tool/action 引用。

  Run transient controls
    当前 run 的临时控制状态，存在 Redis。
    例如 active run lock、cancel flag、stream cursor、tool lease、短期 cache。

  Long-term memory
    从用户确认、业务事件或对话摘要中沉淀出的可复用用户偏好和长期事实。
    可存在 Postgres、向量索引或专用 memory store，但必须带 owner、source、confidence 和 consent。

Derived model input
  Context projection
    本轮投给模型看的最小上下文，由 ledger、workflow state、业务表、memory 和 tool result
    派生出来。
    它不是权威状态，不进入普通对话历史；每轮请求重新构造，用完即可丢弃或只保存引用。
```

### P1：Session state 职责过载，workflow 生命周期缺失

当前 `ChatSession + ContextState` 实际上承担了一个“大 session state”的角色。它同时保存：

- 会话连续性：`previous_response_id`、`loaded_skill_ids`。
- prompt/context cache：`loaded_references`、`loaded_tools`、`last_assistant_message`。
- workflow state：`birth_journey_intake`、`milk_management_state`。
- session slots：`profile_slots`、`birth_prep_slots`。
- UI / 设备辅助状态：`client_events`、`available_tool_images`、`last_displayed_tool_image`、`active_device_module`。
- 服务路由提示：`active_service_domain`。
- run control：`run_lock`、`active_run_id`、`cancelled_run_ids`。

这导致旧系统里的“状态机”不是一等 workflow，而是散落在 session dict、tool handler 和 prompt 注入逻辑中：

- 奶量管理流程的 `analysis_intake` 只有在 `milk_analysis_intake_manage` 等工具执行后才写入 `milk_management_state`，流程结束后没有统一 terminal lifecycle。
- 孕期计划流程的 `birth_journey_intake` 由 `birth_journey_intake_manage` 返回后整体替换，生成计划后也不会自动归档或清理。
- 从奶量管理切到孕期计划时，旧奶量状态仍留在同一个 `ContextState`；系统主要靠 `active_service_domain` 决定本轮注入哪部分 context。
- 有些写操作会局部 invalidation，有些流程结果会继续保留，缺少统一的 `collecting / waiting / completed / expired / archived` 语义。

生产风险：

- workflow 状态没有 owner、schema_version、status、expires_at，无法迁移、恢复、灰度或回放。
- 多个业务流程在同一个 session dict 中并存，容易发生跨流程污染。
- 流程是否结束、是否可继续、是否应重开，依赖隐式代码和 prompt，而不是可测试的状态机。
- 会话状态、业务事实和模型上下文混在一起，难以做权限检查、审计、删除和客服排查。

生产级不应保留“大 session state”。Thread/session 只应保存对话容器和当前活跃 workflow 引用；具体 workflow 进入独立、持久、有 schema 的 workflow state。

### P1：Tool contract 不够正式，工具治理依赖隐式代码约定

当前 tool registry 主要是函数 map 和 schema 集合，虽然已有 `tool_registry.py`、`tool_schemas.py`、`safe_tool_arguments()`、`safe_tool_result()`，但缺少一等 tool metadata。

生产级 tool contract 至少需要声明：

```text
tool_name
domain
read / write
required_permission
owner_scope
side_effect_level
requires_confirmation
idempotency_required
audit_required
timeout
retry policy
safe args policy
safe result policy
blocking policy
```

缺少正式 contract 会导致：

- 模型能调用工具，但系统不知道该如何授权、审计和限流。
- 新增工具时容易遗漏 timeout、幂等、确认、脱敏和错误映射。
- 写工具和读工具边界不清晰，副作用可能绕过 action/audit。
- 前端 tool event 可能暴露过多参数或敏感结果。

生产级应建立 `ToolExecutor`，在调用 handler 前统一执行 permission、owner scope、safe args、timeout、blocking policy 和 audit/action 决策。

### P1：内部 runtime ledger 不完整，过度依赖 provider state

这里的 provider state 指 OpenAI Responses API 这类模型提供方保存或返回的续聊状态，例如当前旧系统使用的 `previous_response_id`。

当前旧实现使用 OpenAI `previous_response_id` 延续多轮对话；这只属于旧系统现状，不进入目标架构，也不作为兼容或兜底路径保留。

当前缺失完整内部账本：

- user message。
- assistant message。
- tool calls。
- tool outputs。
- artifacts。
- action decisions。
- safety decisions。
- stream events。

风险：

- 无法完整审计一次 agent run 做了什么。
- 无法稳定 replay、debug、eval 和客服排查。
- provider session 与业务事实、工具副作用之间没有可验证对应关系。
- 断流、崩溃或服务重启后，很难判断 run 处于 completed、failed、cancelled 还是 waiting_for_confirmation。

重构目标是删除 `previous_response_id` 续聊路径，由系统自己维护 `agent_messages` 会话历史数组，并通过 history selector / context projector 为每次模型调用构造输入。内部权威运行记录应落到：

```text
agent_threads
agent_runs
agent_messages
agent_tool_calls
agent_tool_outputs
agent_artifacts
agent_actions
agent_events
agent_safety_events
agent_context_checkpoints
```

模型请求构造应从内部账本派生：

```text
agent_messages + selected tool/artifact/action context + business facts
  -> history selector / context projector
  -> OpenAI Agents SDK node input
```

这样 provider 只负责本次模型推理，不保存也不恢复 MomCozy 的会话上下文。

### P1：流式和 WebSocket bridge 架构不适合多实例生产

当前 App WebSocket bridge 默认转发到本地 SSE upstream；agent run 用线程执行，response stream 又起 reader thread。

主要问题：

- 多实例部署时，同一 thread/run 必须路由回同一进程，否则会丢状态。
- stream progress 没有持久 cursor，客户端断线后只能弱恢复。
- cancel flag、active run lock、reader thread 状态只在本进程可靠。
- backpressure 和慢客户端处理不清晰。
- 前端 replay 依赖实时流，而不是持久 application events。

生产级应把 AG-UI run 作为一等资源：

```text
agent_runs
agent_events(run_id, sequence, event_id, type, payload)
Redis active run lock
Redis cancel flag
Redis stream cursor / pubsub
replay API
resume stream API
cancel API
```

Flutter / Web 客户端只消费稳定 application events，并通过 `event_id` 或 `sequence` 去重；不要直接绑定 provider raw events、线程状态或本地 SSE upstream。

### P2：部署、配置、CI/CD 基础薄弱

当前缺少生产级：

- Dockerfile / docker-compose local。
- CI workflow。
- typed settings。
- 由环境变量驱动的 PostgreSQL、Redis、OSS 配置切换。
- ruff / mypy / pytest gates。
- health live/ready。
- secrets 管理。
- local / staging / production 环境隔离。

开发阶段可以使用本机 PostgreSQL、Redis、MinIO / 本地兼容对象存储；生产环境切换为托管 PostgreSQL、托管 Redis、云 OSS。切换只能通过环境变量和 typed settings 完成，不能改代码或保留硬编码地址。还需要移除或隔离 demo 行为，例如默认 seed/reset、默认 admin 密码、静态客户端 token。

## 目标生产级架构

目标不是一次性重写所有东西，而是把当前系统升级为可恢复、可权限控制、可审计、可测试、可观测、可多人协作维护的产品工程。

本节按长期稳定的系统边界组织：客户端、后端 API、基础设施配置、权威数据、认证用户域、Agent runtime。具体框架和库应服务于这些边界，而不是反过来决定业务边界。

总体目标：

```text
Flutter App
  -> HTTPS REST / WebSocket
  -> FastAPI API Gateway
      -> Auth / Users / Device Sessions
      -> Agent Runtime
      -> Profile / Baby
      -> Care Plan / Milk / Diary / Device / IBCLC
      -> Files / Notifications / Background Jobs
  -> PostgreSQL
  -> Redis
  -> Object Storage
  -> LangGraph durable workflow orchestration
  -> OpenAI Agents SDK reasoning / tool-loop nodes
```

### Flutter App 目标架构

Flutter 使用 feature-first 结构：

```text
lib/
  app/
    app.dart
    router.dart
    theme.dart
    bootstrap.dart
  core/
    config/
    errors/
    logging/
    network/
    storage/
    security/
    analytics/
    feature_flags/
    permissions/
  shared/
    widgets/
    design_system/
    l10n/
    state/
  features/
    auth/
      data/
      domain/
      presentation/
    agent_chat/
    profile/
    baby/
    care_plan/
    milk/
    diary/
    device/
    settings/
test/
integration_test/
tool/
```

原则：

- 状态管理选择一个主方案，默认 Riverpod，其次 Bloc/Cubit。
- API client 从 OpenAPI 生成或按 OpenAPI contract 手写维护。
- DTO 与 domain model 分离。
- refresh token 和敏感凭证放 secure storage。
- access token 由 session/network 层管理。
- 每个异步页面有 loading、empty、error、success、permission denied、offline。
- Agent UI 只消费稳定 application events，不绑定 provider raw events。
- reducer 通过 `event_id` / `sequence` 去重，通过 `run_id`、`message_id`、`tool_call_id`、`artifact_id`、`action_id` 合并。
- CI 至少覆盖 format、analyze、unit/widget tests。
- 发布流程覆盖 flavor、签名、crash reporting、store 检查。

MomCozy feature map：

```text
features/
  auth/          # 登录、刷新、退出、设备会话
  agent_chat/    # transcript、work panel、artifact、action confirmation
  profile/       # 用户和宝宝 profile 展示/编辑
  care_plan/     # 待产和孕期计划 artifact
  milk/          # 喂养、吸奶、库存、提醒
  diary/         # 孕期日记和媒体附件
  device/        # 产品指导、设备图片、帮助流程
  settings/      # 隐私、通知、账号删除
```

迁移顺序建议：

1. Auth/session。
2. Agent chat。
3. Profile / baby profile。
4. Care plan / milk / diary / device。
5. Settings / privacy / notifications。

### FastAPI 后端目标架构

目标结构：

```text
app/
  main.py
  factory.py
  core/
    settings.py
    logging.py
    errors.py
    security.py
    observability.py
  api/
    dependencies.py
    error_handlers.py
    v1/
      router.py
  modules/
    users/
    auth/
    profiles/
    baby/
    files/
    actions/
    audit/
    agent_runtime/
    care_plan/
    milk/
    diary/
    device/
    ibclc/
    notifications/
  infrastructure/
    db/
    redis/
    object_storage/
    external_clients/
      llm/
  workers/
migrations/
tests/
scripts/
docs/
```

原则：

- app factory + lifespan 管理启动、关闭、连接池和健康检查。
- Router 只做协议适配、依赖注入、认证入口和 schema validation。
- Service/use case 负责业务规则、权限、事务、幂等和流程编排。
- Repository 负责 DB 读写。
- Redis、OSS、OpenAI 和第三方服务都走 client 封装。
- 错误返回稳定 code，不让前端解析自然语言。
- 所有写操作考虑 permission、idempotency、request_id、audit log 和 rollback / compensation。

### 环境与基础设施配置

开发、本地测试、staging、production 使用同一套代码，通过 typed settings 和环境变量切换基础设施：

```text
APP_ENV=local | test | staging | production
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://...

OBJECT_STORAGE_BACKEND=minio | s3 | aliyun_oss
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000
OBJECT_STORAGE_BUCKET=momcozy-local
OBJECT_STORAGE_REGION=...
OBJECT_STORAGE_ACCESS_KEY_ID=...
OBJECT_STORAGE_SECRET_ACCESS_KEY=...
OBJECT_STORAGE_FORCE_PATH_STYLE=true | false
OBJECT_STORAGE_PUBLIC_BASE_URL=...
```

约定：

- local/dev 默认连接本机或 docker compose 内的 PostgreSQL、Redis、MinIO。
- staging/prod 使用托管 PostgreSQL、托管 Redis 和云 OSS，只替换环境变量。
- `.env.example` 只放非敏感示例；`.env.local`、真实 secret、托管服务密钥不进仓库。
- app 启动时 fail fast 校验必需配置；禁用隐式 fallback 到 SQLite、本地文件目录或内存状态。
- 基础设施能力通过 `infrastructure/db`、`infrastructure/redis`、`infrastructure/object_storage` client 封装，业务模块不直接读取 env。
- health ready 检查 DB migration 状态、Redis 连接和 OSS bucket/权限配置；live 只检查进程存活。

### 数据分层

PostgreSQL 是权威数据：

```text
users
user_profiles
auth_identities
user_devices
refresh_tokens
roles
permissions
audit_logs

baby_profiles
feeding_records
pumping_records
growth_records
milk_plans
calendar_tasks
care_plan_artifacts
pregnancy_diary_entries
device_info
device_events

uploaded_files
agent_threads
agent_runs
agent_messages
agent_tool_calls
agent_tool_outputs
agent_artifacts
agent_actions
agent_audit_logs
agent_outbox_jobs
agent_workflow_states
agent_context_checkpoints
agent_context_projections
agent_events
agent_safety_events
```

Redis 只放可重建状态：

- rate limit。
- active run lock。
- cancel flag。
- stream cursor / progress。
- 短期 tool/API cache。
- worker lease / pubsub。

Redis 不保存权威用户资料、已确认 action、agent checkpoint 唯一副本、医疗或业务关键事实。

Object storage 保存大文件和 artifact：

- 用户上传图片、音频、PDF。
- 语音分片。
- 视觉分析输入。
- agent 生成报告、卡片、长工具结果。
- 设备日志、导出文件。

Postgres 保存 metadata：

```text
file_id
owner_user_id
bucket
object_key
content_type
size
checksum
purpose
created_at
deleted_at
```

### 用户管理与认证目标

目标模块：

```text
auth
  register / login / refresh / logout
  password reset
  email/phone verification
  oauth identity
  device session

users
  user base record
  profile
  account deletion
  privacy export
```

策略：

- access token 短期有效。
- refresh token 轮换、按设备存储、可撤销。
- 前端只保存 token，不把 profile 当认证事实。
- 后端所有业务 API 用 `current_user` 决定 user scope。
- 设备或内部服务调用使用 service key，但必须和用户 token 分离。

### Agent Runtime Pattern Decision

MomCozy 新 runtime 采用 **LangGraph + OpenAI Agents SDK**，不再保留自研 Responses API adapter 或旧 agent loop 作为生产兜底。

选择理由：

- LangGraph 负责 durable orchestration、workflow state、checkpoint、interrupt、resume、cancel、retry 和 graph version。
- OpenAI Agents SDK 负责 graph 节点内的模型推理、tool loop、specialist agents、guardrails、handoff 和 tracing。
- FastAPI `AgentRuntimeService` 负责 run lifecycle、权限、action/audit/outbox、event persistence、stream/replay 和业务 service 调用。
- OpenAI provider session、SDK session、LangGraph checkpoint 都不能替代内部 `agent_*` runtime ledger。

明确拒绝：

- **SDK-only**：不足以承载奶量分析、孕期计划、确认中断、断线恢复和多实例恢复等长流程。
- **LangGraph-only**：会把模型/tool loop、specialist agent、guardrail 和 tracing 细节重新压回自研代码。
- **legacy adapter / 自研 Responses adapter**：只代表旧系统现状，不进入目标架构，也不作为灰度、回滚或生产兜底选项。

### Agent runtime 目标架构

对当前 MomCozyAgent，目标不是保留旧 Agent 会话方案，而是迁移到一套新的 production runtime。模型调用只能通过 OpenAI Agents SDK 节点和受控 provider client 封装进入系统；不保留旧的 `previous_response_id` 续聊、进程内 `ChatSession`、provider session、本地 SSE bridge、自研 Responses adapter 或直接 SQLite 写入路径作为兜底。

保留的是产品能力和协议意图，不是旧实现：

- service skill manifest 的按需加载思想，但要版本化、持久化，并纳入 context projector。
- `tool_search` + deferred namespace 的渐进加载思想，但要提升为正式 tool contract。
- AG-UI / WebSocket 事件体验，但要改为持久 `agent_events` + replay/resume contract。
- `safe_tool_arguments()` / `safe_tool_result()` 的脱敏原则，但要升级为统一 safe payload policy。

明确移除：

- `previous_response_id` 续聊路径。
- 进程内 `ChatSession` 作为会话权威的路径。
- provider session 作为上下文来源的路径。
- 自研 Responses API agent loop / adapter 作为 runtime 路径。
- 本地 SSE upstream bridge 作为生产流式兜底的路径。
- tool handler 直接写 SQLite 或绕过 service/action/audit 的路径。

目标方案：

- Postgres 保存 runtime ledger：`agent_threads`、`agent_runs`、`agent_messages`、`agent_tool_calls`、`agent_tool_outputs`、`agent_artifacts`、`agent_actions`、`agent_audit_logs`、`agent_events`。
- Postgres 保存 workflow/checkpoint：`agent_workflow_states`、`agent_context_checkpoints`。
- Postgres 可记录每轮输入投影的摘要或引用：`agent_context_projections` 或 `agent_runs.input_context_ref`，用于 replay、debug 和 eval；它不是新的权威状态源。
- `agent_messages` 维护会话历史数组；history selector 只选择用户消息、助手消息、必要 tool summary 和 resource ref，不选择旧 context projection。
- `ContextProjector` 从 ledger、workflow state、业务表、memory、tool/action refs 派生本轮 OpenAI Agents SDK 节点输入。
- Redis 只保存 transient controls：active run、cancel flag、stream progress、临时锁。
- 删除 `ContextState` 大字典；业务事实回到业务表，workflow state 进入 `agent_workflow_states`，模型输入上下文只由 per-run projection 生成或记录。
- 所有 agent 写操作进入 action proposal：

```text
preview
-> confirmation
-> apply
-> audit log
```

### Session / Workflow State 目标模型

重构后不再设计一个可无限膨胀的 session state。持久状态和临时运行控制按归属拆开：

```text
thread-scoped
  agent_threads：对话容器、owner、title、status、active_workflow_id。

run-scoped
  agent_runs / agent_tool_calls / agent_events：一次执行发生了什么、是否等待确认、是否失败或取消。

workflow-scoped
  agent_workflow_states：奶量分析、孕期计划、设备指导等可恢复流程的状态机。

business-scoped
  milk records、milk plans、birth journey care plans、diaries、files、device data。

user-scoped
  profile、偏好、长期记忆、授权信息。

transient
  Redis lock、cancel flag、stream cursor、tool lease。
```

`model context` 不属于状态存储。它是 `ContextProjector` 每轮从上述状态源、业务事实、长期记忆和工具结果中派生出的输入视图，详见下一节。

建议新增 `agent_workflow_states`，用于承载旧系统中 `birth_journey_intake`、`milk_management_state.analysis_intake` 这类流程状态：

```text
agent_workflow_states
  id
  thread_id
  owner_user_id
  run_id
  workflow_type
  status
  schema_version
  state_json
  active_step
  created_at
  updated_at
  completed_at
  expires_at
```

示例 workflow：

```text
workflow_type = milk_analysis_intake
status = collecting | ready_to_evaluate | evaluated | preview_ready | applied | paused | expired
state_json = current_field, checklist, collected_slots, workflow_control, assessment_ref, plan_preview_ref

workflow_type = birth_journey_intake
status = collecting | waiting_checkup | ready_to_generate | generated | blocked_by_symptoms | completed | paused | expired
state_json = basic_info, next_step, completed_groups, personalization_summary, generated_plan_ref
```

流程切换时，不清空其它流程字段，而是更新 `agent_threads.active_workflow_id`，把旧 workflow 标记为 `paused`、`completed` 或 `expired`。流程结束时，权威结果写入业务表，workflow state 只保留可恢复、可审计、可回放的过程状态和业务资源引用。

### Context Projection 与 Prompt Cache 策略

`context projection` 是派生模型输入，不是新的状态存储层。每一轮模型请求都应重新构造 `context projection`，但不应把完整 state 或上一轮 state dump 当作普通对话历史继续传入。

推荐模型输入结构：

```text
stable_system_prompt
stable_developer_prompt
stable_tool_schemas
stable_skill_or_workflow_rules
selected_conversation_history
current_state_projection
fresh_business_facts
memory_projection
current_user_message
```

规则：

- `agent_messages` 只保存用户消息、助手消息、必要 tool summary / ref；不保存旧的 `current_state_projection`。
- 每轮从最新 `agent_workflow_states`、业务表、memory 和 tool/action refs 重新生成 `current_state_projection`。
- 第 N+1 轮只带最新 state projection，不带第 N 轮的 state projection；需要对比时只带短 `state_delta`。
- `context projection` 可单独保存到 `agent_context_projections` 或 `agent_runs.input_context_ref`，用于 replay、debug 和 eval，但默认不参与 history selection。
- `history selector` 必须过滤 debug system note、旧 context dump、provider raw event 和过期 state projection。
- 大型或敏感 tool output、文件内容、原始记录进入 DB/OSS；模型上下文只放 summary、resource_id、updated_at、freshness 和权限信息。

建议记录本轮 context 投影：

```text
agent_context_projections
  id
  run_id
  thread_id
  context_schema_version
  prompt_version
  tool_schema_version
  selected_message_ids
  active_workflow_state_id
  source_refs_json
  projection_summary_json
  token_estimate
  created_at
```

Prompt cache 优化原则：

- 稳定内容放前缀：system / developer prompt、通用安全规则、稳定 tool schema、skill / workflow rules。
- 动态内容放后缀：selected history、current state projection、fresh business facts、memory projection、当前用户消息。
- tool schema、skill rules 和上下文字段顺序必须稳定；避免每轮随机排序或重写同义摘要。
- `prompt_cache_key` 按 agent / service domain / prompt version 设计，不按 user_id 或 thread_id 设计，例如 `momcozy:agent:v3:zh-CN`、`momcozy:milk-management:v2:zh-CN`。
- `prompt_cache_retention` 默认通过环境变量配置，生产可用 `24h`；如合规、ZDR 或敏感数据策略要求更短保留，则按模型支持切换。
- 共同稳定前缀尽量不包含 PII；用户资料、健康记录、宝宝信息、workflow state 和业务事实放在动态后缀。
- 监控 `usage.prompt_tokens_details.cached_tokens`，按 prompt version、tool schema version、service domain 统计命中率。

事件契约对 Flutter 稳定输出 application events，例如：

```text
run.queued
run.started
run.progress
run.waiting_for_confirmation
message.delta
message.completed
tool.started
tool.completed
action.proposed
action.confirmation_required
action.queued
action.applied
action.failed
artifact.created
run.completed
run.failed
run.cancelled
```

每个事件包含：

```text
event_id
type
sequence
thread_id
run_id
created_at
payload
```

### API 契约目标

先稳定 OpenAPI，再迁移 Flutter。

统一错误模型建议：

```json
{
  "error": {
    "code": "invalid_token",
    "message": "Access token is invalid",
    "status": 401,
    "request_id": "req_xxx",
    "details": {}
  }
}
```

每个 endpoint 或 operation contract 至少声明：

```text
name / route
purpose
request schema
response schema
error schema
auth / permission
owner scope
idempotency
pagination / filtering
side effects
audit requirement
```

### 母婴健康与情绪安全目标

MomCozy 的安全规则应落在后端 guardrail、tool/action contract 和 eval 中：

- deterministic red-flag classifier / rule gate。
- 情绪危机独立升级路径。
- 医疗建议输出审查。
- 高风险场景禁用普通业务流程。
- 人工或专业支持 handoff 记录。
- 安全事件和安全 eval regression。

Flutter 负责展示明确升级、禁用无关 action、避免显示 raw tool args；后端负责强制判断和记录。

## 迁移计划

迁移原则：

- 先契约，后实现。
- 先后端身份和数据边界，后 Flutter 大规模迁移。
- 先持久化 agent runtime，再改复杂 agent orchestration。
- 每次迁移一个 vertical slice；临时兼容层只允许服务迁移窗口，必须有 owner、删除时间和验收标准。
- 不保留旧 agent runtime、自研 Responses adapter、provider session 或内存 `ChatSession` 作为生产兜底。
- Redis 只放可重建状态，Postgres 承担权威事实。
- 高风险写操作必须先 action 化。

### Phase 0：冻结现状与契约盘点

产出：

- 当前 API 清单。
- 当前 SQLite schema 清单。
- 当前前端页面和 feature 清单。
- 当前 agent event contract。
- 当前 tool schema 和 side effect 清单。
- 当前业务关键路径测试基线。
- 健康红旗、情绪危机、权限越权、工具误用 golden eval。

原则：

- 不立刻删 React/Capacitor。
- 不立刻重写 agent loop。
- 不立刻引入大规模新框架。
- 先把边界、风险和验收标准固定。

### Phase 1：生产 FastAPI 骨架

任务：

- 建立 `app/` 分层结构或在现有包内分层迁移。
- 引入 typed settings、request id、统一错误模型、CORS、rate limit、health checks。
- 建 `.env.example` 和环境变量矩阵，覆盖 local/test/staging/production 的 DB、Redis、OSS、OpenAI、CORS、日志和安全配置。
- 接 PostgreSQL、Redis、OSS client。
- 建 Alembic migrations。
- 建 Docker / compose local：Postgres、Redis、MinIO；本机开发也可通过相同 env 指向本机安装的 PostgreSQL / Redis / MinIO。
- 建 CI：format、lint、type check、unit、integration、migration tests。

验收：

- `/health/live` 和 `/health/ready` 可用。
- DB migration 可重复执行。
- Redis / OSS 连接检查可用。
- 测试环境可独立配置。
- local/staging/prod 基础设施只通过环境变量切换，不改代码。
- production 环境禁止 fallback 到 SQLite、本地文件目录、内存 Redis 替代物或 demo seed/reset。
- 前端不再收到内部异常字符串。

### Phase 2：用户管理与认证

任务：

- 建 users/auth/device sessions。
- 实现 register/login/refresh/logout/me。
- 实现 refresh token rotation。
- 将 `ENTRY_API_KEY` 降级为内部 service key。
- 所有业务 API 从 `current_user` 获取 user scope。
- 文件 metadata 增加 `owner_user_id`。

验收：

- 过期 access token 被拒绝。
- refresh token 轮换后旧 token 失效。
- 越权访问其他用户数据失败。
- 前端不再发送权威 `user_id`。
- WebSocket 鉴权不复用长期 refresh token。

### Phase 3：PostgreSQL 数据迁移与业务模块切分

任务：

- 将 SQLite schema 映射为 PostgreSQL schema。
- 旧 SQLite 主键如需保留，新增 `legacy_id`。
- 拆分 `data_store.py` 为领域 repository。
- 优先迁移 profile、baby、feeding、pumping、growth、plan、diary。
- 引入 audit log、idempotency、request_id。

验收：

- 旧 SQLite 样本数据可导入 Postgres。
- 行数、字段、关键查询结果可比对。
- 核心接口返回与旧接口兼容，或提供明确 v2 contract。
- 所有关键写操作有事务和失败回滚。

### Phase 4：文件与 OSS

任务：

- 新建 files module。
- 上传文件先写 OSS，再写 Postgres metadata。
- 旧本地 `upload_files` 迁移到 OSS。
- 使用 signed URL 或受控后端转发访问文件。

验收：

- 文件上传、读取、删除、权限校验通过。
- metadata 与 object key 一致。
- 删除账号或隐私请求能处理文件生命周期。
- 用户不能通过 `file_id` 读取他人文件。

### Phase 5：Agent runtime 生产化

任务：

- 持久化 agent thread/run/message/tool_call/tool_output/artifact/action/event。
- 新增 `agent_workflow_states`，迁移 `milk_management_state.analysis_intake`、`birth_journey_intake` 等流程状态。
- 新增 `agent_context_projections` 或 `agent_runs.input_context_ref`，保存每轮 context 投影摘要和 source refs。
- 删除 `previous_response_id` 续聊路径及其兼容/兜底代码；模型输入只允许从 `agent_messages`、tool outputs、artifacts、actions 和业务事实构造。
- 移除以进程内 `ChatSession` 或 provider session 作为会话权威的代码路径。
- 实现 `ContextBuilder / ContextProjector` 与 `history selector`，过滤旧 state projection、debug note 和 provider raw event。
- loaded skills、必要 checkpoint 入 Postgres。
- active run、cancel、stream progress 入 Redis。
- 固定采用 LangGraph + OpenAI Agents SDK：LangGraph 管 graph/checkpoint/interrupt/resume，OpenAI Agents SDK 管节点内 reasoning、tool loop、specialist agents、guardrails 和 tracing。
- 不新增或保留自研 Responses adapter；旧 loop 只用于 Phase 0 现状盘点和迁移前对照，进入新 runtime 后必须删除。
- 支持 `prompt_cache_key`、`prompt_cache_retention` 环境变量，并记录 `cached_tokens` 指标。
- tool contract 元数据化：permission、owner scope、side effect、confirmation、idempotency、audit、timeout。
- 所有写操作 action proposal 化。
- tool handler 通过领域 service/repository，不直接写 SQLite。
- 保持 Flutter 所需 application event contract。

验收：

- 服务重启后同一用户同一 thread 可恢复。
- cancel run 生效。
- 奶量管理、孕期计划等流程有独立 workflow status，不再依赖 `ContextState` 大字典延续。
- 每轮只投最新 state projection；旧 projection 不进入普通对话历史。
- prompt cache 前缀稳定，动态 state / business facts / current user message 后置，`cached_tokens` 可观测。
- LangGraph graph version、prompt version、context schema version、SDK agent version 都记录到 run ledger。
- 工具参数和结果不会把敏感数据流给前端。
- 写操作必须 confirmation 后 apply。
- action apply 有 audit log。
- run replay 可支持 QA / support / eval。

### Phase 6：安全 guardrails 与 eval

任务：

- 医疗和情绪 deterministic guard。
- 高风险场景 interrupt / handoff。
- eval runner + CI regression。
- prompt / skill 版本化和灰度。
- 安全事件记录和人工支持闭环。

验收：

- 红旗和情绪危机用例稳定分流。
- 普通业务流程不能绕过高风险 guard。
- 工具误用和越权场景在 CI 中有 regression。
- 高风险回复和 action 有可审计记录。

### Phase 7：Flutter App 骨架与功能迁移

任务：

- 建 Flutter app/router/theme/config/network/secure storage。
- 接 auth 流程和 typed API client。
- 建 agent_chat feature，优先迁移 AG-UI/WebSocket 消费。
- 用 feature-first 迁移页面。

推荐迁移顺序：

1. Auth / onboarding。
2. AgentHub chat + streaming work panel。
3. Profile / baby profile。
4. Records / milk records。
5. Schedule / care plan。
6. Status。
7. PumpSession / device / BLE。
8. IBCLC / media / voice。

验收：

- 登录后进入 App。
- token 刷新无感。
- agent chat 流式展示与旧 React 行为一致或有明确产品变更。
- 核心页面有 loading、empty、error、success、permission denied、offline。
- reducer 断线重连和重复事件不重复渲染。

### Phase 8：灰度、切换与清理

任务：

- 新旧前端并行一段时间。
- 后端只在迁移窗口保留明确命名的临时 API compatibility layer；不保留 runtime adapter 或旧 agent loop 兜底。
- 关键用户路径灰度发布。
- 数据双写只在必要窗口开启，并明确关闭时间。
- 下线 SQLite、本地文件存储、静态客户端 API token。
- 清理 React/Capacitor 旧代码或归档。
- 清理 legacy user id、debug localStorage 身份、临时兼容 API / data bridge。

验收：

- 线上关键指标无明显回退。
- 错误率、延迟、token 成本、DB/Redis/OSS 指标可观测。
- 有回滚方案。
- 旧系统依赖被明确移除或归档，生产路径中不存在旧 runtime adapter、自研 Responses adapter 或 `ChatSession` 兜底。

## Phase -> Skill Reference -> PR Slice 执行映射

本节把上面的迁移阶段落到可执行 PR 切片。Reference 是实施该阶段前应读取的全局 skill 文档；PR slice 是建议的最小合并边界，不要求一次 PR 完成整个 phase。

执行规则：

- 一个 PR 只改变一个 durable boundary，例如 schema、repository、auth dependency、tool wrapper、stream adapter、graph node 或 eval suite。
- 大范围文件移动和行为变化尽量拆开。
- 临时兼容层必须写明 owner、删除条件和删除 PR；不允许把旧 agent runtime、自研 Responses adapter、provider session 或内存 `ChatSession` 留作生产兜底。
- 每个 PR 必须带对应验收：contract test、migration test、security test、stream replay test、eval 或手工验收清单。

| Phase | 必读 skill reference | 推荐 PR slice |
| --- | --- | --- |
| Phase 0：冻结现状与契约盘点 | `$production-fastapi-backend-architecture/references/complete-backend-refactor.md`；`$production-fastapi-backend-architecture/references/demo-antipatterns.md`；`$production-agent-architecture/references/migration-playbook.md` | PR-00A：API / DB / tool / event / state inventory 文档；PR-00B：baseline smoke / contract tests；PR-00C：健康红旗、情绪危机、权限越权、工具误用 golden eval 初版 |
| Phase 1：生产 FastAPI 骨架 | `$production-fastapi-backend-architecture/references/app-factory-settings.md`；`$production-fastapi-backend-architecture/references/implementation-blueprint-fastapi.md`；`$production-fastapi-backend-architecture/references/engineering-templates.md`；`$production-fastapi-backend-architecture/references/deployment-operations.md` | PR-01：app factory + lifespan + test app fixture；PR-02：typed settings + request_id + logging + error envelope；PR-03：PostgreSQL / Redis / OSS clients + health ready；PR-04：Docker / compose local + CI gates |
| Phase 2：用户管理与认证 | `$production-fastapi-backend-architecture/references/auth-permissions.md`；`$production-fastapi-backend-architecture/references/api-schema-contracts.md`；`$production-fastapi-backend-architecture/references/security-hardening.md` | PR-05：users/auth/device-session schema + migrations；PR-06：register/login/refresh/logout/me + token rotation；PR-07：`current_user` dependency + owner scope tests；PR-08：WebSocket 短期 token 或 header 鉴权 |
| Phase 3：PostgreSQL 数据迁移与业务模块切分 | `$production-fastapi-backend-architecture/references/data-layer-migrations.md`；`$production-fastapi-backend-architecture/references/project-organization.md`；`$production-fastapi-backend-architecture/references/implementation-blueprint-fastapi.md` | PR-09：Alembic baseline + SQLite schema mapping；PR-10：profile/baby repository + service + tests；PR-11：feeding/pumping/growth repository + service + tests；PR-12：plan/diary/device 模块切分；PR-13：旧 SQLite 样本导入和比对脚本 |
| Phase 4：文件与 OSS | `$production-fastapi-backend-architecture/references/project-organization.md`；`$production-fastapi-backend-architecture/references/security-hardening.md`；`$production-fastapi-backend-architecture/references/deployment-operations.md` | PR-14：files module + owner-scoped metadata schema；PR-15：OSS client + upload/download/delete flow；PR-16：本地 `upload_files` 迁移脚本 + signed URL / 受控转发；PR-17：文件越权和删除账号生命周期测试 |
| Phase 5：Agent runtime 生产化 | `$production-agent-architecture/references/implementation-blueprint-fastapi.md`；`$production-agent-architecture/references/state-and-persistence.md`；`$production-agent-architecture/references/api-and-schema-contracts.md`；`$production-agent-architecture/references/tool-action-contracts.md`；`$production-agent-architecture/references/streaming-resume.md`；`$production-agent-architecture/references/prompt-context-management.md`；`$production-agent-architecture/references/migration-playbook.md` | PR-18：agent thread/run/message/tool/event ledger schema + stores；PR-19：event store + replay/stream/cancel contract；PR-20：tool metadata registry + `ToolExecutor`；PR-21：action/audit/outbox + first protected write；PR-22：LangGraph graph factory + first workflow checkpoint；PR-23：OpenAI Agents SDK reasoning node + specialist boundary；PR-24：`ContextProjector` + prompt cache metrics；PR-25：删除 `previous_response_id`、自研 Responses adapter、`ChatSession` runtime path |
| Phase 6：安全 guardrails 与 eval | `$production-agent-architecture/references/security-and-permissions.md`；`$production-agent-architecture/references/testing-eval-harness.md`；`$production-agent-architecture/references/eval-observability.md`；`$production-agent-architecture/references/memory-system.md` | PR-26：deterministic red-flag / emotion crisis guard；PR-27：safety events + handoff record；PR-28：eval runner + CI critical suites；PR-29：prompt / skill / graph version rollout gates；PR-30：长期记忆最小闭环和删除/同意策略 |
| Phase 7：Flutter App 骨架与功能迁移 | `$flutter-product-app-architecture/references/complete-flutter-build.md`；`$flutter-product-app-architecture/references/migration-playbook.md`；`$flutter-product-app-architecture/references/api-state-auth.md`；`$flutter-product-app-architecture/references/agent-streaming-ui.md`；`$flutter-product-app-architecture/references/testing-release.md` | PR-31：Flutter bootstrap / flavor / router / theme / network；PR-32：auth/session + secure storage + token refresh；PR-33：typed API client + error mapper；PR-34：agent_chat reducer + stream replay UI；PR-35：profile/baby/milk/care_plan/diary/device vertical slices；PR-36：release/test gates |
| Phase 8：灰度、切换与清理 | `$production-fastapi-backend-architecture/references/release-zero-downtime-migrations.md`；`$production-fastapi-backend-architecture/references/deployment-operations.md`；`$production-agent-architecture/references/deployment-operations.md`；`$production-agent-architecture/references/testing-eval-harness.md` | PR-37：staging/canary release plan + rollback runbook；PR-38：双写/临时兼容层关闭；PR-39：下线 SQLite、本地文件、静态客户端 token；PR-40：删除 legacy user id/debug 身份/临时兼容 API；PR-41：生产 SLO dashboard + incident replay runbook |

## 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 前后端同时重写导致范围失控 | 进度不可控、回归多 | 先契约，后按 vertical slice 迁移 |
| React 行为迁移到 Flutter 时遗漏边界状态 | 用户体验回退 | 为旧关键路径录制验收清单和截图/视频 |
| SQLite 到 Postgres 数据类型/时区差异 | 数据错误 | migration dry-run + 抽样比对 |
| 静态 API key 迁移到用户 auth 破坏设备/服务调用 | 设备上报失败 | 用户 token 和 service key 分通道迁移 |
| agent 内存 session 持久化不完整 | 多轮上下文丢失 | 先持久化 thread/run/checkpoint，再迁移业务工具 |
| Redis 被误用为权威状态 | 数据丢失 | 关键状态只进 Postgres，Redis 只存可重建状态 |
| OSS 权限错误 | 文件泄露或不可访问 | signed URL、owner_user_id 校验、对象 metadata 审计 |
| WebSocket 鉴权设计不当 | token 泄露或连接失败 | 优先 header；必要时短期 WS token |
| 业务 API 仍信任请求体 user_id | 越权 | 所有 user scope 从 `current_user` 注入 |
| 医疗/母婴安全边界在重构中丢失 | 高风险错误建议 | 后端 guardrail、tool/action policy、安全 eval 三层覆盖 |

## 测试策略

### 后端

- Unit：service/use case、repository、auth token、权限、错误映射。
- Integration：Postgres、Redis、OSS、OpenAI client mock。
- Contract：OpenAPI schema、Flutter API client 兼容。
- Migration：SQLite 样本导入 Postgres，行数、字段、关键查询比对。
- Security：越权、过期 token、refresh 重放、账号枚举、文件越权。
- Worker：outbox retry、DLQ、lock lease、graceful shutdown。

### Agent

- Request：request 构造、context projection、prompt/skill version。
- Tool：tool dispatch、permission、timeout、safe args/result。
- Action：proposal、confirmation、apply、audit、idempotency。
- Streaming：event order、cancel、断线重连、replay。
- Eval：红旗、情绪危机、权限越权、工具误用、action confirmation。

### Flutter

- Unit：API client、repository、state notifier/bloc、token refresh。
- Widget：loading、empty、error、success、permission denied、offline。
- Integration：登录、agent chat、记录新增、计划查看、文件上传。
- Device：BLE、语音、通知、后台任务、deep link。
- Golden/screenshot：核心页面迁移前后对照。

### 端到端

- 新用户注册登录 -> 完善 profile -> 开始 agent 对话。
- 上传图片/文件 -> agent 使用文件 -> 渲染 artifact。
- 添加吸奶/喂养记录 -> 状态页更新 -> agent 读取近期事实。
- 生成计划 preview -> 用户确认 -> apply -> audit log。
- 服务重启 -> 同一 thread 恢复 agent 对话。
- token 过期 -> refresh 成功 -> WebSocket 重连。

## 关键验收标准

生产级重构阶段性完成时，应满足：

- 客户端不再依赖静态 `VITE_API_TOKEN` 作为用户身份。
- 后端所有业务数据按 authenticated user scope 访问。
- PostgreSQL 是权威数据源。
- Redis 可清空后系统仍能恢复关键业务事实。
- OSS 不暴露公开读写权限。
- 开发环境可使用本机 PostgreSQL / Redis / MinIO，生产环境可通过环境变量切到托管 PostgreSQL / Redis / OSS。
- Agent 多轮状态可跨服务重启恢复。
- Agent run、message、tool、artifact、action、event 可审计和 replay。
- Agent runtime 固定为 LangGraph + OpenAI Agents SDK，生产路径中不存在 `previous_response_id` 续聊、自研 Responses adapter、provider session 或内存 `ChatSession` 兜底。
- 现有 AG-UI/WebSocket 事件契约在 Flutter 中有等价 UI 表现。
- 高风险写操作都有 preview、confirmation、apply、audit。
- 医疗/母婴/情绪高风险场景有 deterministic guard 和 eval。
- local/staging/prod 只靠配置切换，不改代码。
- CI 覆盖 lint/type/test/contract/migration/eval 关键路径。
