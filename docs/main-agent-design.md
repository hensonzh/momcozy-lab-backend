# Momcozy 主智能体设计

状态：讨论草案，尚未实施。

## 1. 本轮结论

主智能体第一版采用一个收敛的混合架构：

> **主智能体 + 专业能力工具 + 专业智能体交接**

主智能体不是一个完整的通用工作流引擎，也不需要预先为每轮请求生成复杂任务图。它只承担三项核心职责：

1. 处理通用问题。
2. 调用已下沉为工具的、边界清晰的专业能力，并直接回复用户。
3. 当任务需要完整专业智能体时，将会话交接给对应的专业智能体。

`MainAgentDecision`、`OrchestrationPlan`、`SpecialistTaskEnvelope` 和 `SpecialistTaskResult` 不作为当前方案的强制契约。多专业任务先遵循简单编排规则；只有真实业务出现依赖图、长任务、暂停恢复或复杂重试后，才补充通用工作流模型。

## 2. 文档目标与边界

本文档用于对齐：

- 主智能体与专业智能体的职责边界。
- 哪些专业能力应该作为工具暴露给主智能体。
- 什么情况下必须交接给专业智能体。
- 主智能体第一版真正需要的字段。
- 通用问题、单专业问题和多专业问题的回复所有权。

本文档当前不决定：

- 具体 SDK 或工作流框架。
- 迁移步骤和上线计划。
- 数据库表、REST API 或事件协议的最终实现。
- 每个专业智能体内部的 Prompt、Skill 和 Tool 细节。
- 尚无真实业务需求的通用 DAG 编排协议。

相关文档：

- [Agent 上下文构造方案](./agent-context-construction-design.md)
- [Agent Tool Catalog](./agent-tool-catalog.md)
- [Momcozy M.ai 产品需求](./product/momcozy-mai-integrated-prd.md)

## 3. 简化后的运行结构

```text
用户请求
   │
   ▼
主智能体 Loop
   ├── 通用 Skill / Tool ──────────────┐
   ├── 专业能力 Tool ──────────────────┤── 主智能体回复
   └── 专业智能体 Route / Handoff ─────┘
                         │
                         ├── 单专业：专业智能体直接回复
                         └── 多专业：结果返回主智能体后汇总回复
```

这里的“路由”不要求先额外调用一次分类模型。正常情况下，主智能体可以在同一个 Agent Loop 中通过工具选择或 Handoff 完成路由。

## 4. 三类处理路径

### 4.1 路径 A：主智能体直接回答

适用于不依赖专业领域推理的通用任务，例如：

- 产品能力说明。
- 情绪接纳和一般性陪伴。
- 普通寒暄、澄清和对话承接。
- 使用主智能体通用 Skill 或通用 Tool 就能完成的任务。

最终回复者：主智能体。

### 4.2 路径 B：调用专业能力工具后回答

适用于具有一定专业性，但目标单一、边界清晰、通常能在一轮内完成的任务，例如：

- 一般健康知识咨询。
- 某个症状的风险提示和就医建议分级。
- 查询一项奶量记录并做简单解释。
- 查询某个设备步骤或故障码。

专业能力工具返回结构化领域结果，主智能体结合用户问题和会话语境生成最终回复。

最终回复者：主智能体。

### 4.3 路径 C：交接给专业智能体

适用于需要专业智能体持续拥有任务的场景，例如：

- 需要多轮收集专业信息。
- 需要维护独立的领域状态或执行完整工作流。
- 需要连续调用多个专业 Tool 并根据中间结果调整计划。
- 需要在后续多轮持续跟踪同一专业目标。
- 风险或复杂度已超过主智能体专业能力工具的边界。

单专业目标的最终回复者：该专业智能体。

多个专业目标的最终回复者：主智能体。各专业智能体以“返回结果”模式工作，主智能体负责去重、消歧和组织答案。

## 5. 专业能力工具与专业智能体的边界

专业能力是否做成 Tool，不取决于它是否“专业”，而取决于它是否是一个**有界能力**。

| 判断维度 | 专业能力 Tool | 专业智能体 |
| --- | --- | --- |
| 任务范围 | 单一、边界明确 | 开放、可能动态扩展 |
| 对话轮次 | 通常一轮可完成 | 通常需要持续多轮 |
| 状态 | 无状态或轻状态 | 持有专业流程状态 |
| 工具使用 | 固定查询或有限推理 | 自主选择多个专业工具 |
| 结果形态 | 结构化结果返回主智能体 | 用户回复或结构化专业结论 |
| 回复所有权 | 主智能体 | 单专业时由专业智能体拥有 |
| 失败处理 | 返回错误或升级建议 | 在专业流程内追问、恢复或降级 |

### 5.1 Tool 不一定是普通函数

如果一个“健康咨询工具”内部需要模型进行受约束的领域推理，它在实现上可以是一个 **agent-as-tool** 或独立专业能力服务，而不是确定性函数。

对主智能体来说，它仍表现为一个边界稳定的 Tool：

- 输入 Schema 明确。
- 输出 Schema 明确。
- 不接管用户会话。
- 结果返回主智能体。
- 可以明确要求升级到专业智能体。

### 5.2 不复制专业逻辑

专业能力 Tool 和对应专业智能体应复用同一套领域核心能力，包括：

- 安全策略。
- 专业知识与检索源。
- 底层业务 Tool。
- 术语和输出约束。
- 风险分级和升级规则。

不能把专业 Prompt 和规则复制一份到主智能体中，否则主智能体会逐渐重新变成一个大而全的单智能体。

## 6. 回复所有权

回复所有权按当前轮需要整合的专业目标计算：

| 场景 | 执行方式 | 最终回复者 |
| --- | --- | --- |
| 通用问题 | 主智能体直接处理 | 主智能体 |
| 一个或多个有界专业能力 | 主智能体调用能力 Tool | 主智能体 |
| 一个完整专业目标 | Handoff 给一个专业智能体 | 专业智能体 |
| 多个完整专业目标 | 调用多个专业智能体并收集结果 | 主智能体 |
| 需要澄清才能判断 | 主智能体追问 | 主智能体 |
| 触发特殊安全规则 | 按安全策略执行 | 安全策略指定 |

一个用户请求包含多个意图，不等于一定需要多个专业智能体。例如“看看最近奶量，再给我一般性的喂养建议”可能只需要两个有界能力 Tool，仍由主智能体完成。

## 7. 主智能体职责边界

### 7.1 主智能体负责

- 作为用户请求的统一入口。
- 维护当前会话的通用语境。
- 在直接回答、调用专业能力 Tool 和 Handoff 之间选择。
- 调用一个或多个有界能力 Tool，并整合结果。
- 为 Handoff 选择目标专业智能体并投影最小必要上下文。
- 处理澄清、安全升级、能力不可用和明确降级。
- 多专业目标时收集专业结果并生成统一回复。

### 7.2 主智能体不负责

- 持有所有专业知识、专业 Prompt 和专业工作流。
- 默认拥有所有专业底层 Tool 的直接权限。
- 代替专业智能体维护长期专业状态。
- 在专业能力失败时自行补造专业结论。
- 修改专业能力返回的风险级别或安全限制。
- 为尚未出现的复杂场景预先维护通用任务图。

## 8. 第一版字段设计

### 8.1 `MainAgentDefinition`

`MainAgentDefinition` 是主智能体的静态、可版本化配置。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `agent_id` | `string` | 是 | 稳定机器标识，建议沿用 `cozymate_service_agent` |
| `version` | `string` | 是 | 主智能体定义版本 |
| `instructions_ref` | `string` | 是 | 主智能体核心指令引用 |
| `model_profile_ref` | `string` | 是 | 模型和推理参数配置 |
| `general_skill_ids` | `list[string]` | 是 | 主智能体可按需加载的通用 Skill |
| `general_tool_names` | `list[string]` | 是 | 主智能体可直接调用的通用 Tool |
| `capability_tools` | `list[CapabilityToolDescriptor]` | 是 | 暴露给主智能体的有界专业能力 |
| `specialist_routes` | `list[SpecialistRouteDescriptor]` | 是 | 可交接的专业智能体目录 |
| `safety_policy_refs` | `list[string]` | 是 | 全局安全规则引用 |
| `context_policy_ref` | `string` | 是 | 上下文选择和 Token 预算策略 |
| `max_loop_turns` | `integer` | 是 | 单次运行最大模型循环次数 |

不把 `owner`、`status`、`description` 等注册表元数据放进模型运行契约；它们可以继续存在于管理面配置中。

示例：

```yaml
agent_id: cozymate_service_agent
version: draft
instructions_ref: prompts/cozymate-main
model_profile_ref: main-model-profile-v1
general_skill_ids:
  - emotional-support
general_tool_names:
  - profile.read
  - conversation_history.image.load
capability_tools:
  - name: health.general_consult
    domain: health
    contract_ref: capability/health-general-consult-v1
  - name: feeding.milk_summary
    domain: feeding
    contract_ref: capability/feeding-milk-summary-v1
specialist_routes:
  - agent_id: milk_management_agent
    route_ref: specialist-route/milk-management-v1
  - agent_id: device_unboxing_agent
    route_ref: specialist-route/device-unboxing-v1
safety_policy_refs:
  - maternal-safety-v1
  - emotional-crisis-v1
  - device-safety-v1
context_policy_ref: main-context-policy-v1
max_loop_turns: 10
```

### 8.2 `CapabilityToolDescriptor`

该描述符只告诉主智能体“何时调用”和“如何理解结果”，不把实现细节塞进上下文。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `string` | 是 | 模型可见的稳定 Tool 名称 |
| `domain` | `string` | 是 | 所属专业领域 |
| `description` | `string` | 是 | 能做什么、不能做什么、何时调用 |
| `input_schema_ref` | `string` | 是 | 输入契约引用 |
| `output_schema_ref` | `string` | 是 | 输出契约引用 |
| `escalation_policy_ref` | `string` | 是 | 何时建议转专业智能体或安全升级 |
| `implementation_ref` | `string` | 是 | 指向共享领域核心能力，不向模型展开 |

专业能力 Tool 的输出至少应表达：

```yaml
status: completed | needs_more_information | escalate | failed
answer_points: []
safety_flags: []
missing_information: []
recommended_specialist_id: null
source_refs: []
```

字段名可按领域扩展，但不能只返回一段无法判断状态的自然语言。

### 8.3 `SpecialistRouteDescriptor`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `agent_id` | `string` | 是 | 专业智能体标识 |
| `domain` | `string` | 是 | 负责的专业领域 |
| `routing_description` | `string` | 是 | 什么任务应该交接给它 |
| `do_not_route_when` | `list[string]` | 是 | 能用有界 Tool 处理的典型反例 |
| `context_profile_ref` | `string` | 是 | 交接时允许投影的上下文范围 |
| `supports_direct_handoff` | `boolean` | 是 | 是否可直接接管用户回复 |
| `supports_result_mode` | `boolean` | 是 | 是否可向主智能体返回结果供汇总 |

### 8.4 `MainAgentTurnContext`

每轮只向主智能体加载工作集，不加载完整会话账本。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `thread_id` | `string` | 是 | 逻辑会话标识 |
| `run_id` | `string` | 是 | 本次运行标识 |
| `current_message` | `Message` | 是 | 当前用户原始消息和附件引用 |
| `actor_scope` | `ActorScope` | 是 | 用户、宝宝、设备和权限范围 |
| `selected_history` | `list[Message]` | 是 | 与当前任务相关的原始历史 |
| `working_state` | `object` | 是 | 当前任务和已确认事实 |
| `active_flow` | `object \| null` | 是 | 已在执行的专业流程摘要 |
| `relevant_result_refs` | `list[ResultRef]` | 是 | 可按需恢复的 Skill/Tool 原始结果引用 |
| `safety_context` | `object` | 是 | 本轮必须遵守的安全限制 |

Skill 加载结果和 Tool 调用结果继续按实际调用顺序写入本轮 Loop。超出上下文预算后，可以压缩展示，但原始事件和结果必须保留可恢复引用。

### 8.5 `HandoffRequest`

只有真正交接给专业智能体时才创建该对象：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `target_agent_id` | `string` | 是 | 目标专业智能体 |
| `user_goal` | `string` | 是 | 要完成的用户目标，不是简单关键词 |
| `reason_code` | `string` | 是 | 为什么 Tool 已不足以处理 |
| `context_projection` | `object` | 是 | 完成任务所需的最小上下文 |
| `reply_mode` | `direct \| return_result` | 是 | 直接回复用户或返回主智能体 |
| `return_to_agent_id` | `string \| null` | 是 | `return_result` 时为主智能体 ID |

模型的普通 Tool Call 使用现有 Tool 协议即可，不需要再包一层 `MainAgentDecision`。

## 9. 健康咨询示例

### 9.1 主智能体调用能力 Tool

用户：

> 哺乳期有点轻微头痛，一般可以先怎么处理？

处理：

1. 主智能体调用 `health.general_consult`。
2. Tool 返回一般护理建议、用药边界、风险信号和升级条件。
3. 主智能体结合用户语境生成回复，但不得删除风险提示或修改风险等级。

该请求不需要把整个会话交给健康专业智能体。

### 9.2 交接健康专业智能体

用户：

> 头痛已经三天了，今天视线模糊，血压也比平时高，帮我持续判断一下。

该请求需要进一步收集信息、持续专业判断并触发更严格的安全策略。健康能力 Tool 应返回 `escalate`，主智能体随后交接给健康专业智能体或按安全规则进行紧急升级。

## 10. 多专业目标的最小编排规则

第一版不要求主智能体输出通用 DAG，只采用以下顺序：

1. 先把可用专业能力 Tool 完成的目标留在主智能体内。
2. 如果只剩一个需要持续处理的专业目标，Handoff 给该专业智能体直接回复。
3. 如果仍有两个及以上完整专业目标，专业智能体以 `return_result` 模式执行，结果回到主智能体汇总。
4. 如果目标之间存在真实依赖，先执行提供前置事实的目标，再执行依赖目标。
5. 如果缺失一项信息会同时阻塞多个目标，由主智能体统一向用户追问。

只有出现以下情况，才引入显式 `OrchestrationPlan`：

- 三个及以上任务存在非平凡依赖。
- 任务需要长时间运行、暂停和恢复。
- 需要持久化重试、补偿或人工审批。
- 需要对中间状态提供稳定外部 API。

## 11. 上下文管理原则

- 所有智能体共享同一个逻辑 `thread_id` 和统一事件账本。
- 主智能体与专业智能体不共享完全相同的模型上下文。
- Handoff 只传完成目标所需的最小上下文投影。
- Agent Loop 中实际加载的 Skill 和 Tool 结果按调用顺序保留。
- 模型上下文可以裁剪、压缩或只保留关键字段，但不得丢失原始结果引用。
- 专业智能体产生的新事实写回统一账本，供后续按需投影。
- 不把全部专业 Tool 定义和完整历史长期塞给主智能体。

### 11.1 图片附件

图片采用“MomCozy 对象存储 + 稳定内部 `asset_id` + 会话内稳定 HTTPS
签名 URL”的单一方案：

1. App 先上传图片到 MomCozy 对象存储；上传成功并取得 `asset_id` 前，发送按钮保持不可用。
2. API、消息账本和 Agent 上下文只保存 `asset_id` 与必要的展示元数据，不保存 Base64 或签名 URL。
3. 后端为 `thread_id + asset_id` 持久化唯一的签名 URL 租约；租约有效期内每轮都复用完全相同的 URL。
4. 只有 Provider Adapter 在请求模型前，把内部 `asset_id` 临时物化为 Responses API 的 `input_image.image_url`；物化结果不回写上下文。
5. 默认租约为七天。到期后的下一次调用生成并持久化一个新 URL，此后继续稳定复用；因此 URL 变化只发生在租约续期边界。
6. 文件所有权、图片 MIME 类型和 HTTPS URL 在服务端校验，签名 URL 绑定表不通过业务 API 暴露。

这样，历史上下文中的图片引用保持短小和稳定，签名参数不会污染模型上下文；同一租约内 Provider 输入前缀也不会因每轮重新签名而破坏缓存命中。

## 12. 第一版不做什么

为了控制架构复杂度，第一版明确不做：

- 为每轮请求强制生成意图树或任务 DAG。
- 为普通 Tool Call 增加额外决策 Schema。
- 允许专业智能体之间自由对话或相互无限 Handoff。
- 把所有专业 Tool 同时暴露给主智能体。
- 把完整事件账本原样放入每一次模型上下文。
- 为尚未验证的未来场景预建通用流程平台。

## 13. 必须保持的系统约束

1. 专业能力 Tool 必须有明确边界和升级条件。
2. 专业能力 Tool 与专业智能体不得维护两套冲突的领域规则。
3. 主智能体不得覆盖 Tool 或专业智能体返回的安全限制。
4. Handoff 必须携带用户目标和最小必要上下文，不能只传一个分类标签。
5. 单专业 Handoff 后，主智能体不再重复生成一份用户回复。
6. 多专业汇总时，主智能体只能组织、比较和解释专业结果，不能补造专业事实。
7. 原始会话、Skill 加载和 Tool 结果写入统一事件账本并可恢复。
8. 写操作继续遵守权限、确认、幂等和审计要求，不因多智能体而绕过。

## 14. 与当前实现的关系

当前 `cozymate_service_agent` 可以继续作为主智能体稳定 ID。现有通用 Tool Catalog、按需 Skill 加载和上下文投影机制可以复用。

后续真正需要新增的核心抽象只有两类：

1. 有界专业能力的 `CapabilityToolDescriptor`。
2. 专业智能体的 `SpecialistRouteDescriptor` 与 Handoff 机制。

多专业任务的持久化编排不是开始迁移前必须定稿的前置条件。

## 15. 待继续讨论

后续建议按以下顺序继续：

1. 健康咨询中，哪些问题属于 `health.general_consult` 的能力边界。
2. 健康能力 Tool 应返回哪些结构化字段和安全信号。
3. 奶量管理和设备开箱分别在哪个条件下从 Tool 升级为专业智能体。
4. 专业智能体以 `direct` 和 `return_result` 两种模式运行时，回复契约是否需要不同。
5. 首批专业智能体目录及每个智能体的 Handoff 条件。
