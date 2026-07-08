# Agent Tools

这里维护模型可调用工具的工程契约。

## 文件职责

- [registry.py](registry.py)：注册 tool contract，包括工具名、领域、权限、读写类型、确认策略、超时和 schema ref。
- [schemas.py](schemas.py)：维护 tool input JSON schema，并通过 `tool_input_schema(schema_ref)` 暴露给 SDK runner。
- [handlers.py](handlers.py)：实现 tool handler，把模型调用转成业务 service 调用、action proposal 或 artifact 写入。
- [executor.py](executor.py)：统一执行工具，负责权限校验、schema 校验、超时、tool_call/tool_output/event 落库。
- [contracts.py](contracts.py)：定义 `ToolContract` 元数据结构。

## 查找方法

找一个工具时按这个顺序：

1. 在 `registry.py` 找工具名和 schema ref。
2. 在 `schemas.py` 找该 schema ref 的输入字段。
3. 在 `handlers.py` 找同名 handler 的业务实现。
4. 如果工具创建 action，再去对应业务模块的 `agent_actions.py` 找 outbox apply 逻辑。

Tool schema 只描述模型输入，不等于完整业务 API schema；业务 API 的请求响应仍由各业务模块自己的 `schemas.py` 维护。
