# 看便便专项服务：单向、无状态分析流

**工程状态（2026-10-23）：端到端代码链路已实现，真实生产能力默认关闭。** 独立服务可调用轻量视觉模型并流式返回严格结构化结果；Product 仍是唯一持久化方。医学效果与儿童图片处理中美发布验收未完成，不能仅凭工程测试打开生产开关。

## 一条链路

`App 上传原图 -> 独立照片审核 -> Product 提交并落首卡 -> Product worker 取原图、核指纹、生成临时 JPEG -> 独立服务调用视觉模型并流式返回结构化事件 -> Product 验证、持久化并向 App 可重放 SSE -> Agent 每次追问重新授权获取同次图片+完成结果`。

- Product 长期保存私有原图、审核、任务、宝宝最小背景、最终结果和事件；提交时审核须在 180 天有效期内。主动删除/撤销后拒绝迟到事件并物理清理图片。
- 独立服务没有数据库、对象存储凭证和 Product 反向读图 API。模型请求不携带 owner ID、对象 key或公开 URL，且设置 `store=false`。
- 服务合同为 `stool.analyze.v1` / `stool.result.v1` / `stool.events.v1`。完整字段顺序和约束见 [传输契约](stool-specialty-transport.md)。
- App 先展示卡片骨架，再按 Product 已提交事件填充；失败、历史恢复、删除和完成后带图追问均有确定性测试。字段目前在完整视觉模型响应后由专项服务发出，不称为 token 级模型流。
- Agent 只持久化 `{analysis_id,baby_id}` 引用。每次模型调用前都向 Product 重做账号、设备会话、宝宝、对话、结果、照片和撤销检查；图片与结果作为低信任观察材料，不作为指令。

## 配置

- Product：`STOOL_PHOTO_REVIEW_ENABLED`、`STOOL_ANALYSIS_ENABLED`、`STOOL_FOLLOWUP_ENABLED`、`STOOL_SPECIALTY_MARKET`、`STOOL_SPECIALTY_ORIGIN`、`STOOL_SPECIALTY_SERVICE_SECRET`。
- 独立服务：`STOOL_SPECIALTY_ANALYSIS_ENABLED`、`STOOL_SPECIALTY_PROVIDER=openai`、`STOOL_SPECIALTY_OPENAI_MODEL=gpt-5.4-mini`、`OPENAI_API_KEY`；风险和建议由版本化确定性规则生成。
- 所有能力默认关闭；配置错误 fail closed。

## 上线门禁

工程测试证明权限、持久化、事件重放和客户端交互，不证明医学准确性。真实发布仍需要医学团队签署结果规则和评测阈值、隐私团队批准儿童图片处理与留存、中美区域部署及设备弱网/恢复验收。
