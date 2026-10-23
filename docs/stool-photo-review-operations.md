# 看便便照片审核：运行与发布边界（2026-10-23）

**工程实现完成，生产默认关闭。** 照片审核只决定图片是否适合进入看便便分析，不生成医学结论。真实儿童图片处理仍需中美隐私、安全和审核质量验收。

## 接口与 worker

- `GET /v1/stool-photos/capability`：返回服务端权威的 `review_available`、`analysis_available` 与 `followup_available`。追问仅在分析和追问开关均开启时可用；App 不自行推断能力。
- `POST /v1/stool-photos`：能力开启后接收认证的单张原图；要求 `Idempotency-Key`，服务端按真实编码复验 JPEG/PNG/HEIC、≤10 MiB、短边 ≥720px 和解码像素上限。审核关闭时在读取/保存原图前返回 503。
- `GET /v1/stool-photos/{photo_id}/review`：仅 owner 可读审核状态、原因码、策略版本和有效期。
- `DELETE /v1/stool-photos/{photo_id}`：立即写删除墓碑；worker 可重试物理删除对象。
- `python -m app.workers.stool_photo_reviews`：持久领取待审核照片，核 SHA-256，生成去 EXIF 派生图，调用审核 provider 并写回 `passed/rejected/inconclusive/failed`；同时处理显式删除墓碑。

审核通过凭证可用于**新分析提交**的窗口为 180 天；已关联分析的原图不会因审核到期自动清理，以支持长期带图追问。审核到期且从未关联任何分析的上传会被写删除墓碑并由 worker 物理清理，避免响应丢失形成永久孤儿数据。提交时 Product 会再次核验 owner、删除状态、有效期和原图指纹。

## Decisions 审核策略

`STOOL_PHOTO_REVIEW_ENABLED=false`、`STOOL_PHOTO_REVIEW_PROVIDER=disabled` 为默认值。启用 `decisions` 还要求 `STOOL_PHOTO_THIRD_PARTY_APPROVED=true`、`OPENAI_API_KEY` 和配置模型（默认 `gpt-6-luna`）。

策略分别判断：便便主体、颜色/形状清晰度、光线，以及宝宝面部、隐私部位和其他敏感信息。明确不符合时拒绝；所有正向条件高置信满足且敏感项低置信时通过；中间区间返回 `inconclusive`。provider 错误、超时、结果缺失或对象被篡改一律 fail closed。阈值属于工程默认值，真实发布前仍须用经同意的儿童图片标注集校准。

## 运维要求

- 不在日志、指标、错误回执或普通 CI 工件中保存图片、EXIF、base64、供应商原始响应或概率。
- worker 未运行时不得开放上传能力，否则照片会长期停留在 pending。
- 生产发布须演练：worker 重启、对象写入后数据库失败、删除重试、账号注销、第三方不可用、审核策略撤销和跨区域隔离。
