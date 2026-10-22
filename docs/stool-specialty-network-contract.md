# 看便便专项服务：单向、无状态分析流（合成验证）

**当前状态（2026-10-22）：真实医学分析与儿童照片处理均未开放。** 独立服务只有合成测试可启用的 `POST /v1/analyze/stream`，目前只返回非医学 `stool.analysis.started` / `stool.analysis.failed`，绝不返回医学结论或“成功”。旧的 `/v1/jobs`、独立任务数据库、服务反向读取 Product 原图/快照、双向 JWT 和远端取消墓碑已从运行时移除。未发布的旧迁移已收敛为 `0030`：Product 直接建立本地队列、宝宝快照，并允许非医学进度事件；不会创建旧 outbox 表。若任何环境已经执行过旧 `0030–0034`，不可直接部署此迁移，须先做版本兼容处理。

## 一条链路

`App 上传 -> 独立照片审核 -> Product 审核准入/提交并落首卡 -> Product worker 取原图、核指纹、生成临时 JPEG -> 独立服务流式返回结构化事件 -> Product 验证、持久化、向 App 可重放 SSE -> Agent 按次授权获取同源图片+完成结果`。

- Product 是唯一的**长期**存储：私有原图、审核、任务、宝宝最小背景快照、结果和有序事件。原图审核在提交时须有效（180 天窗口）；同一用户/宝宝/会话作用域、1–3 张归属和审核指纹二次校验。主动删除/撤销后，迟到的分析事件不入账；私有原图由可重试清理 worker 删除。独立服务没有数据库、对象存储凭证、Product 反向读取 API，也不长期存图。
- 当前 `SyntheticStoolDispatchPump` **仅用于合成数据测试**，没有部署启动入口。Product `StoolAnalysis` 自身携带状态、尝试数、到期/租约；失败可有限重试，旧租约和跨区域事件拒绝。提交时首卡与照片链接、宝宝快照、幂等记录和审计由 Product 同一事务落库。`0030` 不补发升级前旧合成任务。
- 服务合同为严格的 `stool.analyze.synthetic.v1`：`analysis_id`、固定部署市场 `CN|US`、展示语言 `zh|en`、宝宝最小快照和 1–3 张有序派生 JPEG（每张 ≤2 MB、去 EXIF；原图/对象 key/公开 URL/owner ID 不传给服务）。Product 调用须 HTTPS、短时 HS256 服务令牌，绑定单个分析、市场和 `purpose=stool.analyze`。服务验签、校验输入；只在 `APP_ENV=test` 且显式开关时接入，生产即使误置开关也拒绝启动。错误响应不回显图片或 base64。
- Product 记录 `stool.card.created` 序号 1；独立服务的测试流依次产生序号 2 `stool.analysis.started`、序号 3 `stool.analysis.failed`；旧合成失败卡仍可只有序号 2 的失败事件。Product 仅接受同分析、严格序号/类型、无额外字段的事件；逐条提交后 App 用 Product 的游标重放，短断流重试不会重复入账进度。医学字段/`completed` 不能绕过校验器写入。断流、无效事件、超时不伪造成功。

## 边界与验证

独立镜像 `services/stool_specialty/Dockerfile` 只包含专项服务；`/healthz` 不要求身份，`/v1/analyze/stream` 要求作用域令牌。独立 OpenAPI 与 DTO 快照在 `docs/contracts/`。合成测试：`test_stool_specialty_stateless.py`、`test_stool_specialty_client.py`、`test_stool_specialty_dispatch_pump.py`、`test_stool_analysis_submission.py`、`test_stool_analysis_submission_postgres.py`；原图审核、历史、删除、Agent 逐次授权有各自测试。跨服务生产部署、医学模型、真实图片、颜色保真、日志与区域隐私批准、设备 UAT **均未通过**。

后续只有医学团队签署结果字段/验证规则、隐私团队批准中美儿童照片处理与留存、专项影像验证/临床评测、双区域部署及设备联调后，才可新增临床流事件、Product 完成结果校验器，并打开 App 分析与 Agent 追问门禁。当前只有非医学进度和失败；Product 的 App 事件接口为有限页 SSE，App 定期续接，不是已验收的医学字段级实时推送。此合成链路不是完整用户体验闭环，更不是医疗建议。
