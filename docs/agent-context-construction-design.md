# Agent 上下文构造方案

状态：方案留档，尚未实施。

## 1. 目标与约束

本方案解决两个问题：

1. 保证长对话、话题切换、运行失败或取消后的对话连续性。
2. 保证存在固定服务工作流时，智能体能够从当前步骤继续，而不是依赖聊天历史猜测流程状态。

本阶段约束：

- 不修改现有系统提示词。
- 不考虑 Prompt、Skill、工具或上下文结构的版本管理。
- 不使用关键词表、正则规则或关键词到 Skill/工作流的映射。
- 对话状态用于帮助模型理解上下文，不作为业务事实或工作流状态的权威来源。
- 固定工作流的当前状态仍以数据库记录为准。

## 2. 当前实现

当前每轮请求构造的模型输入可以概括为：

```text
稳定系统提示词

最近 40 条用户和助手消息

runtime_context
- user_context
- memory
- workflow_context
- working_context
  - skills
  - ongoing_work
  - known_information
  - client_events

当前用户原始消息和附件
```

当前实现已经具备以下能力：

- 从客户端上下文构造当前时间、用户时区、语言、消息发送时间和地理位置。
- 当前用户文本按原始形式进入模型；图片在上下文中只保留稳定 `asset_id`，由 Provider Adapter 在调用前临时解析为 HTTPS 签名 URL。
- 每轮从数据库重新读取活动工作流并构造 `workflow_context`。
- 从活动工作流生成简洁的 `ongoing_work`。
- 在 Redis working context 中短期保留已加载的 Skill 和工具查询结果。
- 加载长期 Memory 快照。
- 加载近期客户端结构化事件。

当前方案的主要局限不是缺少上述运行上下文，而是没有显式保存对话语义状态。模型每轮需要重新从最近消息中判断：

- 用户当前想完成什么。
- 哪些信息已经明确。
- 智能体正在等待用户回答什么。
- 哪些普通对话任务仍未完成。

当相关消息离开最近 40 条消息的窗口后，这些信息可能丢失。数据库工作流可以恢复，但没有工作流承载的普通对话任务无法稳定恢复。

## 3. 目标结构

保留当前 `ContextProjection`，只增加 `conversation_state`：

```python
class ContextProjection:
    stable_system_prompt: str
    selected_conversation_history: list[dict]
    user_context: dict
    conversation_state: dict
    memory_projection: list[dict]
    workflow_context: list[dict]
    working_context: dict
```

目标模型输入为：

```text
稳定系统提示词

最近完整对话轮次和必要的历史证据消息

runtime_context
- user_context
- conversation_state
- memory
- workflow_context
- working_context
  - skills
  - ongoing_work
  - known_information
  - client_events

当前用户原始消息和附件
```

此次调整不重构现有 `user_context`、Memory、Skill、`ongoing_work`、`known_information`、客户端事件或工作流投影。

## 4. 各部分的构造方式

### 4.1 当前用户消息与附件

- 从本轮 run 对应的持久化用户消息读取。
- 保留原始文本，不做摘要。
- 当前图片附件以 `asset_id` 多模态引用进入 Agent 上下文，不存储 Base64 或签名 URL。
- 历史消息继续保留同一个 `asset_id`；Provider Adapter 在实际调用模型时复用 `thread_id + asset_id` 对应的持久化 URL 租约。

### 4.2 `user_context`

包含：

```json
{
  "current_time": "构造本轮上下文时的当前时间",
  "message_sent_at": "客户端发送消息的时间",
  "timezone": "Asia/Shanghai",
  "locale": "zh-CN",
  "location": {
    "country": "CN",
    "region": "Shanghai",
    "city": "Shanghai",
    "latitude": 31.2,
    "longitude": 121.4
  }
}
```

构造规则：

- 只使用客户端明确提供的时区、语言和位置信息。
- `current_time` 按用户时区计算。
- 缺少位置时不通过 IP、聊天文本或模型进行推测。
- 不使用关键词匹配。

### 4.3 `memory`

- 用于跨 thread 的稳定用户背景和偏好。
- 不保存订单状态、工作流步骤等可从业务系统读取的事实。
- 本阶段保持现有 Memory 投影方式。
- 后续如需相关性检索，采用语义检索或模型判断，不采用关键词规则。

### 4.4 `working_context.ongoing_work`

- 每轮从数据库中的活动工作流重新生成。
- 只包含尚未结束的工作流、当前阶段和建议下一步。
- 不从聊天文本中猜测工作流状态。
- `ongoing_work` 是简洁的恢复提示；详细执行约束仍由 `workflow_context` 提供。

### 4.5 `workflow_context`

- 每轮从数据库中的活动工作流状态重建。
- 合并可信的表单提交、当前附件情况和显式工作流回复关系。
- 当前消息与工作流的绑定优先使用客户端回复关系、表单标识或工作流回复元数据，不根据消息中的关键词绑定。
- 工作流当前步骤、已采集字段、当前问题和下一步约束以数据库投影为准。
- 对话状态归纳器不能修改工作流状态。

### 4.6 `working_context.known_information`

- 只保存成功工具调用明确声明需要保留的信息。
- 保留信息来源、使用说明和现有 TTL/预算规则。
- 普通聊天中的用户陈述不直接写入 `known_information`，而是进入对话状态候选。
- 业务事实需要执行动作前，仍应按工具规则检查是否需要重新查询。

### 4.8 `working_context.client_events`

- 继续读取近期持久化客户端事件。
- 只接受结构化事件，不从自然语言反向猜测客户端状态。
- 表单提交、卡片交互等事件可以辅助判断当前消息与某个流程步骤的关系。

## 5. 对话状态

### 5.1 数据结构

对话状态按 thread 持久化：

```python
class ConversationState:
    current_goal: dict | None
    confirmed_information: list[dict]
    pending_items: list[dict]
    processed_through_sequence: int
```

示例：

```json
{
  "current_goal": {
    "content": "完成返工后的母乳喂养计划",
    "evidence_message_ids": ["user-message-id"]
  },
  "confirmed_information": [
    {
      "content": "用户计划下个月 15 日返工",
      "source_type": "user_statement",
      "evidence_message_ids": ["user-message-id"]
    }
  ],
  "pending_items": [
    {
      "id": "pending-item-id",
      "type": "question",
      "content": "确认工作期间预计多久可以挤奶一次",
      "waiting_for": "user",
      "evidence_message_ids": ["assistant-message-id"]
    }
  ],
  "processed_through_sequence": 128
}
```

### 5.2 构造方式

不使用关键词匹配。使用一个只负责结构化语义归纳的 `ConversationStateReducer`：

```text
旧 ConversationState
+ 尚未处理的用户和助手消息
+ 本轮工具结果
+ 本轮工作流和 Action 事件
-> 结构化 ConversationStateDelta
```

Reducer 只输出增量：

```python
class ConversationStateDelta:
    set_current_goal: dict | None
    add_confirmed_information: list[dict]
    add_pending_items: list[dict]
    resolve_pending_item_ids: list[str]
```

不让模型每轮重新生成整个状态，从而减少摘要漂移和旧信息被覆盖。

### 5.3 状态写入规则

- `current_goal` 只能来自用户明确表达的请求、调整或选择。
- 用户临时询问其他问题时，不自动删除尚未完成的原目标。
- `confirmed_information` 只能来自用户明确陈述、结构化表单或成功工具结果。
- 模型推测、建议和常识不能写成用户已确认事实。
- `pending_items` 包含等待用户回答的问题、等待确认的操作和未完成的普通对话任务。
- 工作流未完成事项由数据库 workflow 和 `ongoing_work` 负责，不复制为另一份工作流状态。
- 每项新增内容必须携带证据消息或工具/事件引用。
- 后端校验证据引用必须真实存在，并且属于当前 thread 和用户。
- 没有有效证据的状态增量不写入。

### 5.4 执行时机与失败恢复

- 正常情况下，在本轮完整回复和工具执行结束后异步更新对话状态。
- 每轮构造上下文前，检查 `processed_through_sequence` 后是否存在未处理消息或事件。
- 如果存在遗漏，则在进入主模型前补做一次 Reducer 更新。
- 用户消息即使对应的 run 最终 `failed` 或 `cancelled`，仍然可以在下一轮被处理。
- 未完整生成的助手消息不用于创建新的确认信息或待回答问题。
- Reducer 失败时保留旧状态，并在下一轮重新补算；不能清空已有状态。
- 工作流恢复独立读取数据库，不依赖 Reducer 是否成功。

## 6. 历史消息选择

当前按最近 40 条消息截取。目标方案改为：

```text
最近若干完整用户/助手轮次
+ current_goal 引用的必要历史消息
+ pending_items 引用的必要历史消息
```

选择规则：

- 当前用户消息永远保留原文。
- 最近历史按完整轮次选择，避免从一个轮次中间截断。
- 通过状态中保存的消息引用固定必要旧消息，不做关键词相关度判断。
- 工作流当前状态从数据库投影，不通过扩大聊天历史来恢复。
- 超出上下文预算时，优先丢弃没有被状态引用的更早普通对话。

## 7. 当前方案与目标方案的差异

| 内容 | 当前方案 | 目标方案 |
| --- | --- | --- |
| 用户时间、时区、位置 | 已有 | 不变 |
| 当前消息和附件 | 已有 | 不变 |
| Memory | 已有 | 暂时不变 |
| `known_information` | 工具结果短期保留 | 不变 |
| `ongoing_work` | 从活动 workflow 生成 | 不变 |
| `workflow_context` | 每轮从数据库重建 | 不变 |
| 客户端事件 | 已有 | 不变 |
| 历史消息 | 最近 40 条消息 | 最近完整轮次和状态引用消息 |
| 当前目标 | 主模型每轮临时推断 | thread 级显式状态 |
| 已确认信息 | 分散在历史消息 | 结构化保存并携带证据 |
| 待解决问题 | 主模型每轮临时推断 | 显式保存和显式解决 |
| 长对话连续性 | 依赖相关消息仍在窗口内 | 原消息离开窗口后仍可恢复 |
| 普通任务失败恢复 | 主要依赖剩余聊天历史 | 可从持久化对话状态恢复 |

新方案对固定工作流的改变较小，因为当前工作流已经由数据库持久化并在每轮重建。主要收益发生在没有数据库工作流承载的长对话、话题切换和普通任务恢复场景。

## 8. 最小实施范围

本阶段只需要：

1. 给 `ContextProjection` 和模型输入增加 `conversation_state`。
2. 增加 thread 级 ConversationState Store。
3. 增加基于模型结构化输出的 `ConversationStateReducer`。
4. 将历史选择从最近固定条数改为最近完整轮次加状态证据消息。
5. 增加 Reducer 失败补算和 `failed`/`cancelled` 连续性测试。

本阶段不需要：

- 重写现有工作流系统。
- 修改现有系统提示词。
- 修改 Skill 加载方式。
- 引入关键词分类器。
- 引入 Prompt、Skill、工具或上下文版本管理。
- 为此单独引入新的工作流框架。

## 9. 验收场景

- 超过当前历史窗口后，普通对话中的未完成目标仍能恢复。
- 用户中途切换话题后，可以继续原来的未完成任务。
- 用户只说“继续”时，模型能够依据对话状态或唯一活动工作流恢复正确事项。
- `run.failed` 或 `run.cancelled` 后，下一轮仍能处理此前的用户请求。
- Reducer 异步更新失败后，下一轮可以根据消息序列补算。
- 没有证据的模型推测不会写入已确认信息。
- 用户临时提出无关问题不会推动活动工作流。
- 当前工作流阶段始终来自数据库，而不是聊天历史或 ConversationState。
- Skill 选择和对话状态构造均不依赖关键词映射。
