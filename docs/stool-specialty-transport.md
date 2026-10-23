# 看便便服务传输契约 v1

独立服务只接收 Product 主动发送的临时派生 JPEG 与最小宝宝背景，不反向取图、不建任务库、不长期存图。权威模型位于 `services/stool_specialty/contracts.py`，Product 通过 `app/modules/stool_analyses/transport_contract.py` 复用同一契约；快照为 `docs/contracts/stool-specialty-transport.v1.json` 和 `stool-specialty.openapi.v1.json`。

- 输入 `stool.analyze.v1`：`analysis_id`、`market=CN|US`、`language=zh|en`、宝宝背景，以及 1–3 张有序 `{photo_id,position,original_sha256,jpeg_b64}`。不传 owner ID、对象 key、公开 URL 或 EXIF 原图。
- 独立服务默认使用可配置的轻量视觉模型 `gpt-5.4-mini`，通过 Responses structured output 生成 `stool.result.v1` 候选结果；请求 `store=false`。模型、超时与开关分别由 `STOOL_SPECIALTY_OPENAI_MODEL`、`STOOL_SPECIALTY_REQUEST_TIMEOUT_SECONDS`、`STOOL_SPECIALTY_ANALYSIS_ENABLED` 配置。
- Product 已持久化序号 1 `stool.card.created`；服务依次返回序号 2 `stool.analysis.started`、序号 3–8 六个 `stool.field.ready`（颜色、性状、可见特征、关注程度、解读、建议），最后序号 9 `stool.analysis.completed`。模型或契约失败返回安全的 `stool.analysis.failed`，不回显供应商原文。
- Product 对分析 ID、市场、序号、字段类型、最终结果 Schema、字段与终态一致性重新校验后才落库并向 App 重放。App 不从自由文本推断状态。

运行时能力默认关闭。打开真实入口需要同时配置照片审核、Product 分析开关、独立服务、区域身份密钥和 Agent 追问开关；医学效果、儿童图片隐私和中美区域发布验收仍是上线门禁。
