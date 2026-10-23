# 看便便照片审核：运行与发布边界（2026-10-23）

**工程实现完成，缺少凭据时拒绝新提交。** 照片审核只决定图片是否适合进入看便便分析，不生成医学结论。真实儿童图片处理仍需中美隐私、安全和审核质量验收。

## 接口与 worker

- `GET /v1/stool-photos/capability`：返回服务端权威的 `review_available`、`analysis_available` 与 `followup_available`。新提交由照片审核及专项分析凭据决定；已有结果追问由 Agent 服务凭据和逐次授权决定，App 不自行推断能力。
- `POST /v1/stool-photos`：服务配置齐全后接收认证的单张原图；要求 `Idempotency-Key`，服务端按真实编码复验 JPEG/PNG/HEIC、≤10 MiB、短边 ≥720px 和解码像素上限。服务未配置时在读取/保存原图前返回 503。
- `GET /v1/stool-photos/{photo_id}/review`：仅 owner 可读审核状态、原因码、策略版本和有效期。
- `DELETE /v1/stool-photos/{photo_id}`：立即写删除墓碑；worker 可重试物理删除对象。
- `python -m app.workers.stool_photo_reviews`：持久领取待审核照片，核 SHA-256，生成去 EXIF 派生图，调用审核 provider 并写回 `passed/rejected/inconclusive/failed`；同时处理显式删除墓碑。

审核通过凭证可用于**新分析提交**的窗口为 180 天；已关联分析的原图不会因审核到期自动清理，以支持长期带图追问。审核到期且从未关联任何分析的上传会被写删除墓碑并由 worker 物理清理，避免响应丢失形成永久孤儿数据。提交时 Product 会再次核验 owner、删除状态、有效期和原图指纹。

## Decisions 审核策略

照片审核固定使用 Decisions 和 `gpt-6-luna`；没有独立功能开关，缺少专用 `STOOL_DECISIONS_API_KEY` 时不可提交。

策略分别判断：便便主体、颜色/形状清晰度、光线，以及宝宝面部、隐私部位和其他敏感信息。明确不符合时拒绝；所有正向条件高置信满足且敏感项低置信时通过；中间区间返回 `inconclusive`。provider 错误、超时、结果缺失或对象被篡改一律 fail closed。阈值属于工程默认值，真实发布前仍须用经同意的儿童图片标注集校准。

## 运维要求

`make backend-up` now builds/starts `stool-photo-review-worker` **after** the migration; CI override clears `STOOL_DECISIONS_API_KEY` to prevent external calls. The CI smoke job starts the worker after Alembic, verifies the process is running, then executes the PostgreSQL + MinIO integration tests using fresh random schema and bucket; cleanup removes only those test resources. Do **not** use an existing user bucket for these tests. No real pediatric photos, third-party review or production deployment is involved.

For manual isolated validation against the existing *local* infrastructure (creates/drops only randomized schema and bucket):

```bash
MOMCOZY_TEST_DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy \
MOMCOZY_TEST_OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000 \
python -m pytest -q tests/test_stool_photo_postgres_integration.py
```

Tests use generated synthetic images. An object-storage outage keeps the tombstone for retry; a failed rollback cleanup or a process crash before commit requires a future durable orphan reconciliation path. Do not infer availability from a running worker; the API derives admission from its configured credentials.

## Local checks

`python -m pytest -q tests/test_stool_photo_*.py tests/test_openapi_contract.py tests/test_migration_smoke.py`

`python -m ruff check app/modules/stool_photos app/workers/stool_photo_reviews.py tests/test_stool_photo_*.py`

`python -m mypy app/modules/stool_photos app/workers/stool_photo_reviews.py`

No real pediatric photos or external API calls belong in this test suite. SQLite tests cover core behavior; the isolated PostgreSQL + MinIO suite additionally covers owner isolation, a second worker claim, expired-lease takeover, stale result prevention, account revocation and deletion retry. These tests do **not** constitute pediatric review-policy calibration, third-party privacy approval, orphan-object reconciliation, or a medical specialty service.

## Follow-up context implementation slice (2026-10-22)

`app/modules/stool_analyses/` now contains a durable result/photo-link ledger (migration `20261022_0032`), an in-process resolver, and a dedicated Agent-only internal POST endpoint. The endpoint requires the Agent Runtime service identity and rechecks authorization on every request. With synthetic seeded results it checks current account/device session, baby/thread/owner, result/photo state, SHA-256, and retention on every request, then sends only EXIF-stripped in-memory JPEGs and a bounded result. Account deletion revokes access and scrubs the persisted result in the same DB transaction. The Agent typed client and transient model-input adapter are wired to a versioned analysis reference but remain subject to the Product authorization checks. Synthetic tests are not clinical validation or permission to deploy with real credentials.

A completed analysis may continue to resolve the same image **after its submission review window expires** only while that analysis result is separately valid, the original still exists, and owner/baby/thread/session, photo fingerprint, current review status, and revocation checks pass. Review expiration never renews the original approval for a **new** submission. `context_expires_at`, account/photo deletion, review-policy revocation and privacy retention remain independent gates. Current tests use synthetic results, not clinical validation.

- 不在日志、指标、错误回执或普通 CI 工件中保存图片、EXIF、base64、供应商原始响应或概率。
- 配置真实凭据前须确保审核与分析 worker 正常运行，否则照片可能长期停留在 pending。
- 生产发布须演练：worker 重启、对象写入后数据库失败、删除重试、账号注销、第三方不可用、审核策略撤销和跨区域隔离。
