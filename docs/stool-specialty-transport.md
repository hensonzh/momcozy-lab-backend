# 看便便服务的最小传输契约（合成验证）

以 [网络边界](stool-specialty-network-contract.md) 为准。独立服务只接收 Product 主动发送的临时派生 JPEG + 固定宝宝背景，不反向取图、不建任务库。源模型为 `app/modules/stool_analyses/transport_contract.py` 和独立服务副本 `services/stool_specialty/contracts.py`；快照为 `docs/contracts/stool-specialty-transport.synthetic.json` 和 `stool-specialty.openapi.synthetic.json`。

- 输入 `stool.analyze.synthetic.v1`：单个 `analysis_id`、`market=CN|US`、`language=zh|en`、最小宝宝背景、1–3 张连续位置且不重复的 `{photo_id,position,original_sha256,jpeg_b64}`；不含 owner ID、公开 URL 或原图对象 key。JSON Schema 的唯一性和有序性仍需 Pydantic 跨字段检查。
- 输出仅有 `stool.events.v1` 的 SSE：Product 先提交序号 1 `stool.card.created`；当前专项服务只返回序号 2 `stool.analysis.started` 和序号 3 `stool.analysis.failed`（旧合成任务仍可只有序号 2 的失败），不含任意自由文本、医学字段或完成事件。客户端要按分析 ID、序号、类型和严格字段验证，Product 才能入账并重放。
- 生产环境门禁默认关闭；**合成失败不是便便分析结果**。医学字段字典、多图冲突/红旗规则、中英表达、0–1 岁边界、隐私与区域审批完成前，不接受真实儿童图片，也不开放 `field.ready`、`completed` 或 Agent 真实追问。

更新快照：`python -m scripts.export_stool_specialty_transport`、`python scripts/export_stool_specialty_openapi.py`。合成验证用 `pytest tests/test_stool_specialty_*`；独立 PostgreSQL/MinIO 只用于 Product 持久化测试，专项服务不依赖它们。
