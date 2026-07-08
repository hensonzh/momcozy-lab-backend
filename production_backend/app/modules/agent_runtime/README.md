# Agent Runtime Map

这个模块分成两层看：

- 模型侧资产：服务 skill、全局 prompt、tool contract、tool schema。
- 运行时基础设施：run ledger、event stream、worker、outbox、checkpoint、memory、eval。

## 模型侧资产

- 服务 skill 入口：[skills/catalog.py](skills/catalog.py)
- 服务流程文案：[skills/playbooks.py](skills/playbooks.py)
- 全局稳定提示词：[prompts/instructions.py](prompts/instructions.py)
- 每轮上下文构造：[prompts/context_builder.py](prompts/context_builder.py)
- Tool contract 注册：[tools/registry.py](tools/registry.py)
- Tool input schema：[tools/schemas.py](tools/schemas.py)
- Tool handler：[tools/handlers.py](tools/handlers.py)

设计原则：模型侧仍然按 `skill + tools` 理解。`skill` 决定角色、服务流程、交付物和可用工具；`tool contract` 决定权限、schema、确认策略和执行边界。

## 运行时基础设施

- FastAPI 路由：[router.py](router.py)
- 应用服务：[service.py](service.py)
- 数据模型：[models.py](models.py)
- 数据访问：[repository.py](repository.py)
- Agent run 执行：[run_lifecycle/executor.py](run_lifecycle/executor.py)
- 事件流：[event_stream/](event_stream/)
- 安全规则：[safety/](safety/)
- 长期记忆：[memory/](memory/)
- Eval：[evals/](evals/)

`sdk/specialists.py` 目前保留为 SDK runner 的兼容 profile 层。新增或调整业务场景时，优先从 `skills/` 和 `tools/` 入口开始，不要直接把场景提示词写进 executor。
