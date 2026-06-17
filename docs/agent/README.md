# Momcozy Agent 方案文档

本文档描述当前代码里的智能体方案。项目使用 OpenAI Responses API，不使用 OpenAI Agents SDK。

## 当前状态

当前版本已经移除应用侧 router 和内部 routing skills。系统不会在模型调用前预先选择 `intake-clarification`、`safety-escalation` 或任何业务 skill。

现在的服务选择方式是：

1. 首轮把 service skill manifest 暴露给模型。
2. 模型根据用户意图和安全风险自行选择是否调用 `load_skill`。
3. 应用侧只负责受控加载 skill、执行工具、维护上下文和流式返回事件。
4. 安全风险由稳定指令和已加载 skill 正文中的边界共同约束；当前不再保留独立 `risk_evaluate` 工具。

## 目标

Comate 是 Momcozy 的孕育与哺乳伙伴，定位是懂妈妈、能陪伴、能执行服务流程的母婴场景智能体。它不是泛聊天助手，也不是医疗诊断引擎。

当前优先覆盖的服务场景：

- 待产准备：Birth Plan Card、待产包服务
- 奶量管理：吸奶/亲喂计划、奶量总结、个性化问答
- 情绪支持：识别情绪风险，高风险时触发人工转接
- IBCLC 咨询：哺乳顾问 case 收集、摘要、预约或转接
- 吸奶器设备指导：开箱指导、日常使用指导，当前预留检索和支持机制

核心原则：

- service skill 由模型基于 manifest 判断并调用 `load_skill`。
- 应用侧不做预路由，不预先选择内部 skill 或业务 skill。
- 安全风险由稳定指令和已加载 skill 正文中的边界处理；模型需要在高风险场景优先选择 `emotion-support`、`health-consultation` 或其他相关 service skill。
- 回答风格保持：专业但不冷漠、安抚但不哄骗、清晰但不教条。
- 情绪感知是基础能力：先识别恐惧、内疚、羞耻、疲惫、孤立、反复寻求确认等信号，再决定回答密度、安抚方式、是否需要安全确认或风险升级。
- skill 渐进式加载，不一次性把所有 `SKILL.md` 塞进上下文。
- 应用侧执行工具，模型不能直接读任意文件，也不能执行 shell。
- 每轮只发送轻量 `request_context`；用户动态信息由模型按需调用工具读取。
- AG-UI 前端事件只展示脱敏后的过程状态和最终消息。

## 代码结构

当前源码按产品心智收敛为几个核心模块：

```text
src/momcozy_agent/
  agents.py      # 主 Agent、Responses API loop、AG-UI 事件
  static_context.py  # 静态 instructions、安全策略、service skill manifest 拼接
  contexts.py    # 动态 request_context、ContextState
  skills.py      # skill 注册表、manifest、load_skill、read_skill_file、脚本 allowlist
  tool_schemas.py   # Responses tool schema
  tool_registry.py  # tool 暴露策略、deferred namespaces、handler dispatch
  tool_handlers/    # 应用侧 tool handler adapter/mock
  services/         # 业务服务层；当前包含 milk-management 的数据访问和计划/calendar 逻辑
  api/          # App 端 WebSocket adapter；当前包含 AG-UI WebSocket 桥接
  api_app.py    # App 统一 FastAPI app 入口
  server.py      # FastAPI AG-UI SSE endpoint、thread session、共享 agent stream
  config.py      # .env / 配置加载
  types.py       # 共享类型
```

本地聊天 SSE 服务：

```text
src/momcozy_agent/server.py
  GET /
  GET /health
  POST /api/ag-ui
  POST /api/ag-ui-prewarm
  GET /skill-assets/...
```

运行入口：

```bash
.venv/bin/python -u scripts/run_chat_sse.py
```

`momcozy-chat-sse` 是当前控制台入口；`momcozy-chat-ui` 仅保留为旧脚本兼容 alias，实际启动的仍是同一个 SSE 服务，不再提供 HTML web demo。

默认接口：

```text
http://127.0.0.1:8768/api/ag-ui
```

健康检查：

```text
http://127.0.0.1:8768/
http://127.0.0.1:8768/health
```

App 端 WebSocket 桥接入口：

```bash
.venv/bin/python -u scripts/run_all.py
```

默认在 `ENTRY_HOST:ENTRY_PORT` 启动统一 FastAPI 服务，默认 `0.0.0.0:8769`。该服务提供 App 的 `WS /api/ag-ui-ws` 接口、App 新建会话后台预热用的 `POST /api/ag-ui-prewarm`，并通过内部 SSE upstream 复用 `POST /api/ag-ui` agent stream。App 连接 `/api/ag-ui-ws` 后发送与 `/api/ag-ui` 相同的 AG-UI JSON 请求体，桥接层复用同一套 agent stream，并把每个 AG-UI event 作为 WebSocket JSON text frame 返回。这个入口只做传输协议适配，不改变 agent loop、skill 选择、tool registry 或 session 状态。

统一 FastAPI 服务同样提供 `/` 和 `/health` JSON 健康检查入口；它们只用于 smoke test，不恢复旧 HTML web demo。

`/api/ag-ui-prewarm` 使用同一个 `threadId` 执行一轮隐藏、非流式、禁用工具的 Responses 请求，只保存 `ChatSession.previous_response_id`，不向前端生成消息或 work panel 事件。若真实用户消息先完成并写入了同一 session，预热结果会被视为 stale，不覆盖真实对话状态。

确定性 App 操作可以走轻量 HTTP 入口，避免不必要的模型轮次。例如 `POST /api/hospital-bag/cart-update` 复用 `hospital_bag_cart_update` handler，根据当前 `hospital_bag_cart.groups` 和 `args.action/product_sku_id` 返回新的购物车状态；适用于用户已经确认“换成 Air 1 / M9 / S12 Pro Quick”等型号同步，不用于开放式选型或解释。

## 请求链路

用户从前端发送消息后，整体流程如下：

```text
MomCozyApp AgentHub
  -> WS /api/ag-ui-ws
  -> FastAPI bridge / POST /api/ag-ui
  -> FastAPI server.py 解析 AG-UI payload
  -> 按 threadId 获取 ChatSession
  -> 恢复 previous_response_id、loaded_skill_ids、ContextState
  -> run_agent_loop()
  -> build_agent_request()
  -> client.responses.create()
  -> 模型输出文本或 function_call
  -> 应用侧执行工具
  -> function_call_output 回传 Responses API
  -> 文本 delta 和过程事件通过 SSE/WS 返回前端
  -> RUN_FINISHED 作为成功流最后事件
```

`server.py` 保存每个 `threadId` 的会话状态：

- `previous_response_id`
- `loaded_skill_ids`
- `context_state`

前端需要传当前用户消息、稳定 `threadId`、客户端用户 id、locale、timezone 和本条消息发送时间；后端负责维护完整多轮上下文、`previous_response_id`、已加载 skill 和运行态。

## Agent 设计

主逻辑在 `agents.py`。

稳定规则放在 Responses API 的 `instructions`：

- Agent 身份
- 服务边界
- 安全原则
- skill runtime 使用规则
- 工具调用原则
- 响应风格
- service skill manifest
- service selection protocol

动态信息不作为 developer message 重复发送，而是放在 `input` 的 user content part 中。

首轮请求结构：

```python
input = [
    {
        "role": "user",
        "content": [
            {"type": "input_text", "text": request_context},
            {"type": "input_text", "text": "user_message:\n..."},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,...", "detail": "auto"},
        ],
    }
]
```

图片输入是可选的。客户端会把用户选择的图片读成 Base64 data URL，经 `/api/ag-ui-ws` 或 `/api/ag-ui` 传给后端；后端只把最近一轮用户消息里的图片转换成 Responses API 的 `input_image` content part。生产环境建议改为上传到文件/对象存储或 Files API，再传 URL/File ID，避免长期通过 JSON 传大体积 Base64。

工具返回的本地资源 URL 不会被模型自动访问。对 `device_manual_search.relevant_images` 这类官方步骤图，agent loop 先把图片元数据记录到 `ContextState.available_tool_images`；当最终回复实际展示 Markdown 图片时，再记录 `last_displayed_tool_image`、`active_device_module` 和 `shown_step_image_urls`。后续用户明确询问“图上/这张图/对照图/标注/哪个部件”等需要读图的问题时，下一轮模型请求优先参考 `last_displayed_tool_image`，避免从历史图片里用相同编号误猜；必要时才把最多 2 张 `/skill-assets/...` 白名单图片转为 `data:image/...` 的 `input_image`。该能力只读取 `skills/{skill_id}/assets` 下的图片文件，不处理外部 URL、PDF、视频或任意路径；如果当前用户消息已经附带上传图片，则优先用户上传图片，不再自动附加官方步骤图。结构化工具字段仍优先于视觉读取；图片输入只用于补充读取图中文字、标注和部件位置。

Air1 FAQ 图片位于 `skills/device-guidance/assets/air1/faq-images/`。`faq.md` 当前静态引用 `image1.png` 到 `image18.png`；同目录下的 `air1_unboxing_step*.png` 是从旧 `/images/Air_img/` 迁移来的历史兼容资产，可能被旧会话消息直接引用。不要仅因为它们没有被当前 FAQ 正文静态引用就删除；后端会把 `/images/Air_img/...` 映射到该目录。

产前准备共享字段会记录在 `ContextState.birth_prep_slots`。每轮用户消息进入主 agent loop 的同时，后端会启动一个轻量模型 sidecar 异步抽取用户明确说出的 slots；抽取结果标记为 `confirmed`，但只影响后续轮次的 session slots，不直接写入 user profile。孕期计划、待产包和分娩沟通单都会从同一套 session slots 读取默认值，例如孕周、年龄、单双胎、IVF、城市/医院、分娩方式、喂养意向、支持方、复工时间和焦虑点，避免模型漏传导致表单没有预填。长期画像仍由表单确认、孕期计划卡片或 profile 工具等明确写入路径更新。sidecar 默认模型可通过 `MOMCOZY_SLOT_EXTRACTOR_MODEL` 配置；设置 `MOMCOZY_SLOT_EXTRACTOR_DISABLED=1` 可关闭该异步抽取。

`birth_prep_context` 和 `birth_prep_profile_context` 受 `active_service_domain` gate 控制：domain 为空或为 `birth_prep` 时注入，明确为 `milk_management`、`device_guidance` 或 `emotion_support` 时不注入，避免孕期 slots 污染产后或设备服务。App 可通过 `service_domain` / `active_service_domain` / `current_service` 显式传入；服务工具或 `load_skill` 成功后也会更新 session domain。

后续请求依赖 `previous_response_id` 延续对话状态。

## Skill Selection

当前方案没有应用侧 router，也没有内部 routing skill。

普通请求使用稳定的 Responses `tools` 配置：

- instructions 中的 service skill manifest
- 立即可用的核心工具
- `tool_search`
- deferred namespaces 中的业务工具

模型根据用户意图和安全风险自主决定是否调用：

```json
{
  "skill_id": "emotion-support"
}
```

或：

```json
{
  "skill_id": "birth-prep"
}
```

或：

```json
{
  "skill_id": "health-consultation"
}
```

安全判断不再通过 router 预拦截，而是通过：

- `static_context.py` 中的安全规则
- 已加载 service skill 正文中的安全边界和流程边界

高风险情绪场景应加载 `emotion-support`。
孕期、产后、宝宝、哺乳相关的一般健康咨询应加载 `health-consultation`。
需要 IBCLC/哺乳顾问介入时，用户同意后再调用 `ibclc_consult_card_create`。
紧急情况不能被常规服务流程延迟。

## Skill Runtime

skill 是渐进式上下文模块，不是启动时全量 prompt。

初始普通请求暴露：

- 立即可用的 skill runtime tools
- `tool_search`
- deferred business tool namespaces
- instructions 中的 service skill manifest

模型需要根据 manifest 主动调用：

```json
{
  "skill_id": "birth-prep"
}
```

应用侧执行 `load_skill` 后，只加载对应目录：

```text
skills/<skill-id>/SKILL.md
```

同时返回该 skill 可用的：

- references
- scripts
- assets

`read_skill_file` 只能读取 `load_skill` 返回的 references/assets 中允许的文本文件。  
`run_approved_skill_script` 只能执行应用侧注册过的 allowlist handler，不会执行模型生成的 shell 命令。

## Context 设计

上下文逻辑分为 `static_context.py` 和 `contexts.py`。

当前采用 cache-friendly static instructions + 轻量 `request_context`。

### 1. Static Instructions

稳定信息放在 Responses API 顶层 `instructions`，由 `static_context.py` 的 `STATIC_AGENT_INSTRUCTIONS` 生成：

```text
BASE_AGENT_INSTRUCTIONS
可用 Skill manifest
skill_manifests
```

这些内容不放进每轮 `request_context`，以提高 prompt cache 命中：

- Agent 身份和服务边界
- 回复风格和情绪陪伴原则
- 精简安全底线
- 根据 skill manifest 判断是否加载 skill 的自然语言说明
- 根据 tool description/schema 判断是否调用工具的自然语言说明
- 可用 skill manifests

### 2. Request Context

用户首次进入服务时发送完整 `request_context`：

```text
request_context:
locale: zh-CN
timezone: Asia/Shanghai
message_sent_at: 2026-05-05T17:42:10+08:00
```

后续普通轮次只发送本轮消息时间：

```text
request_context:
message_sent_at: 2026-05-05T17:45:03+08:00
```

`message_sent_at` 是每个用户消息的发送时间，包含日期、时间和时区偏移，因此不再单独注入 `current_date` 或 `current_time`。

`locale` 和 `timezone` 是用户环境信息，首轮发送一次。普通对话轮次不重复发送。

### 3. Dynamic Information

用户画像、宝宝画像、服务状态、历史记录和检索结果不主动注入上下文。

模型需要个性化信息时，应按需调用当前真实可用的读取工具：

- `profile_get`
- `milk_management` namespace 下的奶量记录、计划、计划执行情况和 calendar 读取工具

当前方案不再维护 `context_ledger_delta` 或 `context_invalidations`。这样可以避免每轮因为用户资料变化而污染上下文，也减少模型依赖旧事实的风险。

完整 skill 内容只通过 `load_skill` 的 tool output 进入模型历史，并由 Responses API 的 `previous_response_id` 延续。当前方案不再维护 `loaded_skill_context` 或 `loaded_skill_context_delta`。

`loaded_skill_ids` 只作为应用侧状态和前端调试展示，不再决定后续暴露哪些业务工具，也不负责向模型重发 skill 正文。

## Tools 设计

工具 schema、暴露策略和执行 adapter 已拆分为 `tool_schemas.py`、`tool_registry.py` 和 `tool_handlers/`。旧的 `tools.py` 兼容层已移除，代码应直接从这些模块或包入口导入。

当前采用 Responses API 的 `tool_search` + deferred namespaces。每轮顶层 `tools` 配置保持稳定，业务工具 schema 由模型按需通过 tool search 加载到上下文末尾，以减少工具 schema 对 prompt cache 的破坏。

### Core Immediate Tools

这些工具每轮直接可调用：

- `profile_get`
- `list_skills`
- `load_skill`
- `search_skill_assets`
- `read_skill_file`
- `run_approved_skill_script`
- `ui_quick_replies_create`
- `ibclc_consult_card_create`

### Deferred Business Namespaces

业务工具不再按 loaded skill 切换暴露，而是放在 deferred namespaces 中。仅保留当前本地有执行结果的工具；纯占位工具已移除：

- `care_handoffs`：`handoff_summary_generate`，只用于已经决定转接人工或专业支持后的交接摘要；不用于普通建议、设备售后工单、设备排障或购物车调整
- `device_support`：`device_manual_search`、`support_ticket_draft_create`，用于已购/正在使用的 Momcozy 吸奶器或设备说明书、FAQ、排障和售后工单草稿；不用于购买前选型、奶量计划或待产包购物车
- `milk_management`：聚合后的奶量工具，包括 `milk_snapshot_get`、`milk_records_query`、`milk_record_mutate`、`milk_plan_query`、`milk_plan_preview`、`milk_plan_mutate`、`milk_calendar_query`、`milk_calendar_change_preview`、`milk_calendar_mutate`，以及评估类 `milk_assessment_evaluate`、`infant_growth_evaluate`；用于用户自身奶量、喂养、宝宝生长和 calendar 数据，不用于吸奶器选型、设备排障或购物车调整
- `hospital_bag_cart`：待产包购物车工具 `hospital_bag_cart_update`，用于已经进入待产包购物车后的预算上限优化、删除/加回、基础款替换、医院提供、家里已有、数量调整，以及把已推荐的 Momcozy 吸奶器型号同步到购物车；不用于生成待产包清单、独立吸奶器选型或设备排障
- `pump_recommendation`：吸奶器型号选型工具 `hospital_bag_pump_recommend`，用于购买前 Momcozy 吸奶器推荐、型号对比、预算内选择，也可在待产包场景里先选型再同步购物车；不用于已购设备故障/说明书、奶量是否正常或直接修改购物车
- `birth_prep`：产前准备专用产物工具，包括 `birth_plan_form_create`、`labor_communication_card_create`、`birth_journey_plan_card_create`、`hospital_bag_form_create`、`hospital_bag_card_create`；用于已经进入孕期计划、待产包清单或分娩沟通单流程后的表单/结构化内容生成，不用于普通孕期问答

每个 namespace 中的 function 都设置 `defer_loading: true`。模型开始时只看到 namespace 名称和描述；需要具体工具时由 `tool_search` 加载对应 function schema。

当前 `tool_handlers/` 是应用侧工具 adapter 层，负责把 Responses function call 参数转换成业务服务调用，并处理运行时注入、脱敏和写操作边界。`services/` 是业务服务层，承载真实业务逻辑、数据访问和领域校验；例如 milk-management 的记录读写、计划生成、日程调整和日结读取都在 `services/milk_management/`。

- 读类工具从 runtime inputs 返回数据或空结果。
- milk-management 工具从 runtime inputs 注入 `user_id`，模型不需要也不应该提供用户 ID。
- 专用表单工具返回前端可渲染的 form spec。通用 `ui_form_create` handler 仍保留兼容旧流程，但不在 runtime tools 中直接暴露。
- 专用 card 工具返回前端可渲染的 card artifact；前端根据 `card_type` 和 `schema_version` 选择组件。待产包清单和分娩沟通单工具只接受应用侧注入的对应表单提交数据，不能靠模型参数伪造确认结果。用户可见服务名称不使用“卡片”，该词只描述内部渲染形态。
- 尚未接入真实后端的提醒、booking、case 创建等占位工具当前不暴露给模型。

### Tool Responsibility Boundary

工具是模型和外部环境交互的媒介，但不是第二个对话代理。业务 service 可以做确定性的数据聚合、规则计算、边界校验、候选方案生成和事务写入；不应在 tool 内再次调用 LLM 生成用户话术。

- `*_get`：读取事实和记录。
- `*_evaluate`：用固定参考数据和规则返回结构化评估，不输出诊断。
- `*_validate`：返回 `valid`、`violations`、`warnings` 等校验结果。
- `*_preview`：返回候选方案或变更 proposal，不写库。
- `*_apply` / `*_update` / `*_delete`：执行已确认写入，必须有明确确认和必要的幂等键。

最终用户意图判断、补问策略、风险解释、鼓励/安抚话术和回复组织仍由主 Agent 完成。工具返回的 `summary` 或 `message` 只作为结构化结果摘要，不能当作最终 assistant 回复原样透出。

### Reminder 设计

提醒目前不作为 tool 暴露。模型可以在回复中建议用户在 App 内设置提醒，但不能调用 reminder 工具或承诺已经创建提醒。生产环境接入真实提醒后，再重新加入 schema、handler 和确认流程。

## 前端交互

主前端在 `MomCozyApp`。App 通过 `WS /api/ag-ui-ws` 接入统一 API；桥接层复用 `POST /api/ag-ui` SSE agent stream，并把事件作为 WebSocket JSON text frame 返回。前端消费这些 AG-UI 事件：

- `RUN_STARTED`
- `TOOL_CALL_START`
- `TOOL_CALL_ARGS`
- `TOOL_CALL_END`
- `TOOL_CALL_RESULT`
- `ARTIFACT_CREATED`
- `CONFIRMATION_REQUIRED`
- `TEXT_MESSAGE_START`
- `TEXT_MESSAGE_CONTENT`
- `TEXT_MESSAGE_END`
- `QUICK_REPLIES`
- `RUN_FINISHED`
- `RUN_ERROR`

过程状态和最终 assistant 消息分离。

Work panel 的首个可见进度由 Responses streaming function-call 事件驱动：

- 主 AG-UI event 携带 `semantic` 元信息：`phase`、`label`、`visibility`、`merge_key`、`priority`。前端优先使用后端语义；旧事件没有 `semantic` 时，由前端集中 mapper 兜底生成同一套结构。
- 后端在 `response.output_item.added` / `response.function_call_arguments.done` 阶段识别 `function_call`，并尽早发送 `TOOL_CALL_START`。
- Thinking 只由真实 Responses reasoning stream 事件驱动；`RUN_STARTED`、`requesting_model` 和普通文本 idle 不再显示 Thinking。
- 如果真实 reasoning 发生在 assistant text delta 之后，前端显示“正在准备下一步”；`thinking completed` / `failed` 会立即移除该状态。
- `TOOL_CALL_START` 到达后，前端立即创建或更新 work item。
- `TOOL_CALL_ARGS` 默认不改变可见标题，只作为参数已安全摘要的协议事件；缺少 start 时才补建 work item。
- `TOOL_CALL_RESULT` 到达后，前端立即把同一个 work item 标记为完成或失败。
- Work panel 只展示阶段语义，不展示具体工具名；`tool_call_name` 仅用于协议兜底、合并同一条 work item 和归类为读取、评估、预览、保存、等待确认等阶段。
- `ARTIFACT_CREATED` 到达后，前端渲染表单/卡片/工单草稿等结构化 UI。
- `CONFIRMATION_REQUIRED` 到达后，work panel 显示等待确认状态，具体确认动作由对应 artifact 或业务 UI 承载。
- `QUICK_REPLIES` 是最终回复后的快捷输入 UI。模型应通过全局 `ui_quick_replies_create` 工具为每轮最终回复生成 3 个提示；如果模型漏调，后端会补 3 个安全默认提示。该工具事件不展示在 work panel，不进入 assistant 正文。
- Work item 默认只展示简短状态标题；失败时才展示错误详情。
- `CUSTOM momcozy.agent.status` 是主 loop 当前唯一主动发送的状态通道；`ACTIVITY_SNAPSHOT`、`STEP_STARTED`、`STEP_FINISHED` 只保留历史兼容。

App 前端支持在 composer 中附加最多 4 张图片。图片会作为当前用户消息的一部分发送给模型；前端仅做本地预览，不把图片当作工具结果或长期状态保存。

当工具结果包含 `form` 时，前端渲染表单。当前客户端可以把表单提交转换成用户消息回传；涉及强一致写入时，应优先使用结构化 application event，不依赖模型从自然语言里猜测这是不是已确认表单数据。

### 表单提交契约

前端提交表单时，应向后端发送结构化事件：

```json
{
  "type": "form.submit",
  "thread_id": "thread_user_123",
  "form_id": "hospital_bag_intake",
  "confirmed_form_data": {
    "due_date_or_week": "37 weeks",
    "birth_path": "vaginal",
    "feeding_intention": "breastfeeding",
    "hospital_context": "public hospital",
    "first_birth": true,
    "support_person": true
  }
}
```

后端负责校验 `form_id` 是否来自当前 thread 中已创建的表单。传给 Responses API 时，后端应把表单提交包装成明确的当前轮用户输入，而不是作为长期上下文反复注入：

```text
form_submission:
form_id: hospital_bag_intake
confirmed_form_data:
{
  "due_date_or_week": "37 weeks",
  "birth_path": "vaginal",
  "feeding_intention": "breastfeeding",
  "hospital_context": "public hospital",
  "first_birth": true,
  "support_person": true
}
```

模型看到 `confirmed_form_data` 后，可以把这些字段视为用户已确认的信息，并进入对应结构化内容生成步骤。除非字段明显冲突、不安全，或缺少生成对应内容所必需的信息，否则不要重复询问同一组表单问题。

推荐链路：

```text
birth_plan_form_create / hospital_bag_form_create tool result
  -> 前端渲染表单
  -> 用户提交表单
  -> 前端发送 form.submit structured event
  -> 后端校验 form_id 并包装 confirmed_form_data
  -> 当前轮 Responses input 携带 confirmed_form_data
  -> 模型调用对应专用 card 工具生成 card artifact
```

## Birth Prep 当前服务流程

`birth-prep` skill 当前提供孕期计划、待产包清单和分娩沟通单三个产物服务：

待产包清单和分娩沟通单采用同一个表单确认机制：

```text
专用表单工具
  -> 用户确认表单
  -> 后端注入 confirmed_form_data
  -> LLM 调用对应专用 card 工具
  -> 前端按 card.card_json 渲染 HTML/移动端卡片
  -> 可选导出 PNG/PDF
```

`card.card_json` 是系统内部和前端渲染的真实数据源。HTML、PNG、PDF 都只是展示或分享载体。

孕期计划不走表单，但生成前必须先确认孕期、分娩方式和主要支持人；缺信息时 handler 返回 `needs_required_context`，不产生 artifact。

### 分娩沟通单

流程：

1. 用户表达分娩沟通、生产偏好、给医护看的沟通内容等意图。
2. 模型基于 manifest 调用 `load_skill("birth-prep")`。
3. skill 要求先调用 `birth_plan_form_create` 生成前端表单。
4. 用户确认表单后，模型调用 `labor_communication_card_create`。
5. 前端用 `card.card_json` 渲染可分享分娩沟通单。
6. 输出应强调这是沟通单，不替代医院或临床决策。

核心结构包括：

- `owner`
- `birth_preferences`
- `pain_relief_preferences`
- `communication_preferences`
- `baby_after_birth`
- `medical_notes`
- `if_plans_change`
- `questions_for_hospital`
- `missing_fields`

### Hospital Bag Service

流程：

1. 用户表达待产包、入院准备等意图。
2. 模型加载 `birth-prep`。
3. 首轮先做服务邀约，说明会快速确认几项信息来生成更有针对性的待产包清单。
4. 用户确认开始后，先通过对话确认孕周/预产期、复工/外出计划和最担心的问题，再把这些字段作为 `default_values` 传给 `hospital_bag_form_create`。
5. 用户确认表单后，模型调用 `hospital_bag_card_create`。
6. 前端用 `card.card_json` 渲染待产包清单。

核心结构包括：

- `owner`
- `hospital_context`
- `packing_groups`
- `pack_first`
- `already_prepared`
- `missing_or_to_buy`
- `timeline`
- `personalized_notes`
- `missing_fields`

## 安全和隐私边界

必须保持以下边界：

- 不把所有 skill 文件一次性加载进上下文。
- 不允许模型读取任意文件路径。
- 不给模型 shell 权限。
- 不把完整 tool arguments、用户资料、宝宝资料、喂养记录直接推给前端 AG-UI 事件。
- 图片只用于用户当前请求；医疗、哺乳、皮肤、伤口、婴儿健康等图片不能作为诊断依据。
- `RUN_FINISHED` 是成功 AG-UI stream 的最后事件。
- 前端过程状态和最终 assistant 消息保持分离。
- 医疗、哺乳、情绪支持场景不做诊断、不替代专业人员。

## 当前限制

当前项目适合做方案验证和用户测试，不是生产后端。

主要限制：

- business tools 尚未接真实业务系统。
- 写类、提醒、检索、booking、case 创建、support ticket 等工具已从当前可见工具集中移除，接入真实后端后再恢复。
- 表单提交还不是结构化 application event。
- 本地服务已迁移到 FastAPI，但仍是内存 session 和本地验证形态，不是完整生产后端。
- 缺少完整单元测试和观测日志。

## 下一步建议

优先级建议：

1. 把表单提交改成结构化 application event，写入 `service_state`。
2. 为 `run_agent_loop`、`load_skill`、`read_skill_file`、`run_approved_skill_script`、`/api/ag-ui` 增加测试。
3. 接入真实业务 adapter：profile 写入、case、memory、retrieval、reminder、device support。
4. 补齐 birth-prep、milk-management、health-consultation、device-guidance 的 references/assets。
5. 为 FastAPI 入口补齐认证、限流、持久化 session 和生产观测。
