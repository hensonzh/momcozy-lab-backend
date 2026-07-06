# Production Backend Scripts

本目录放显式执行的开发、回归、验收、运维脚本。除 `run_agent_worker.py`
和 `run_outbox_worker.py` 会作为独立 worker 进程入口外，其他脚本不会随着
FastAPI API 服务启动自动运行。

## 使用原则

- API 服务启动入口是 `production_backend.app.main:app`，不会自动遍历或执行
  `scripts/`。
- 本地、staging、production 的基础设施检查通过 `Makefile` 目标显式触发。
- 真实 provider eval、staging smoke、production readiness 属于发布或定时验收，
  不应混入普通 API 启动流程。
- 脚本默认读取 `Settings.from_env()`，运行前需要加载对应 env 文件。

加载本地 env 的通用方式：

```bash
set -a; . production_backend/env/local.env.example; set +a
```

项目默认使用项目级 Python：

```bash
production_backend/.venv/bin/python
```

## 推荐入口

| 场景 | 推荐命令 | 会运行的脚本 |
|---|---|---|
| 本地启动 API | `make backend-local-up` | 不直接运行 `scripts/*.py`，Compose 启动 API 服务 |
| 本地迁移数据库 | `make backend-local-migrate` | Alembic migration，不走本目录脚本 |
| 本地启动 worker | `make backend-local-workers` | `run_agent_worker.py`, `run_outbox_worker.py` |
| 基础设施验收 | `make backend-check-infra` | database / Redis / object storage / product asset checks |
| 后端产品化门禁 | `make backend-productization-status` | `check_productization_status.py` |
| 后端 smoke | `make backend-smoke` | productization status + seed eval |
| Staging smoke | `make backend-staging-smoke` | productization status + infra checks |
| Production readiness | `make backend-production-readiness` | productization status + infra checks |

## 脚本清单

| 脚本 | 用途 | 启动时间 | 启动方式 |
|---|---|---|---|
| `build_product_asset_manifest.py` | 根据本地素材目录生成 `product-assets.manifest.json`，用于产品素材发布流程。 | 新增、删除或重命名官方产品素材时手动运行。 | `python production_backend/scripts/build_product_asset_manifest.py --source-root <asset-dir> --output production_backend/assets/product-assets.manifest.json --object-key-prefix product-assets/device-guidance/assets` |
| `check_backup_restore_hooks.py` | 检查 PostgreSQL 和对象存储的备份/恢复 hook 配置是否完整。 | PR CI、发布前、生产运维检查时运行。 | `python production_backend/scripts/check_backup_restore_hooks.py`; 生产严格检查用 `--strict` |
| `check_database_profile.py` | 检查当前 env 下 PostgreSQL 是否可连接。 | `backend-check-infra`、staging smoke、production readiness。 | `set -a; . <env-file>; set +a; python production_backend/scripts/check_database_profile.py` |
| `check_object_storage_profile.py` | 对当前对象存储执行临时 `put/get/delete`，验证 provider 可用。 | `backend-check-infra`、CI MinIO 集成测试、staging/production readiness。 | `python production_backend/scripts/check_object_storage_profile.py` |
| `check_product_asset_storage.py` | 校验 manifest 里的每个 `object_key` 是否存在于对象存储，且大小等于 `size_bytes`。 | 本地素材同步后、staging/production 素材发布后、`backend-check-infra`。 | `python production_backend/scripts/check_product_asset_storage.py` |
| `check_productization_status.py` | 检查产品化门禁：必备文档、env profile、CI gate、worker 脚本、Makefile target、旧 runtime 扫描等。 | 每次 PR、本地 smoke、发布前。 | `python production_backend/scripts/check_productization_status.py`; 机器读取用 `--json` |
| `check_redis_runtime_controls.py` | 检查 Redis lock、cancel flag、stream cursor 等 agent runtime 控制能力。 | `backend-check-infra`、CI Redis 集成测试、staging/production readiness。 | `python production_backend/scripts/check_redis_runtime_controls.py` |
| `export_openapi.py` | 导出当前 FastAPI OpenAPI schema，用于契约快照比对。 | API 契约变化时手动更新；CI 中生成临时文件并和 docs 快照 diff。 | `python production_backend/scripts/export_openapi.py --output production_backend/docs/openapi.generated.json` |
| `export_api_surface_catalog.py` | 从 OpenAPI 扩展字段导出 App/API/运维/内部接口分类目录。 | API surface 变化时，在导出 OpenAPI 后同步更新。 | `python production_backend/scripts/export_api_surface_catalog.py --openapi-input production_backend/docs/openapi.generated.json --output production_backend/docs/api-surface-catalog.md` |
| `inspect_worker_backlog.py` | 只读查看 durable outbox 和 agent run backlog 数量，不修改状态。 | 本地/线上排查 worker 堆积、发布后观察。 | `set -a; . <env-file>; set +a; python production_backend/scripts/inspect_worker_backlog.py` |
| `inventory_legacy_backend.py` | 盘点旧后端路由、SQLite 表和迁移风险，生成迁移分析文档。 | 旧后端迁移分析时运行；新后端日常回归不需要。 | `python production_backend/scripts/inventory_legacy_backend.py --write` |
| `recover_stuck_agent_runs.py` | 查找长时间停留在 `running` 的 agent run；默认 dry run，`--apply` 后标记失败并清理 Redis 控制状态。 | 运维恢复卡住的 run 时手动执行。 | `python production_backend/scripts/recover_stuck_agent_runs.py --limit 20`; 真正修改用 `--apply` |
| `run_agent_provider_eval.py` | 使用真实 OpenAI provider 运行 agent eval，验证模型、工具、路由、最终回复质量。 | nightly、手动触发、发布前抽样；不适合每个 PR 必跑。 | `python production_backend/scripts/run_agent_provider_eval.py --allow-skip-without-credentials --max-cases 8 --cost-budget-usd 5.00` |
| `run_agent_replay_eval.py` | 用已保存的 replay bundle 对单个 seed case 做回放断言。 | 线上问题复盘、事故回归、专题修复验证。 | `python production_backend/scripts/run_agent_replay_eval.py --replay <bundle.json> --suite <suite> --name <case-name>` |
| `run_agent_seed_eval.py` | 运行确定性的产品 agent seed eval，可输出 JSON/JUnit。 | 每次 PR CI、本地 smoke、改动 agent runtime/工具/路由时。 | `python production_backend/scripts/run_agent_seed_eval.py --output /tmp/agent-seed-eval.json --junit-output /tmp/agent-seed-eval.junit.xml` |
| `run_agent_worker.py` | 独立 agent run worker 进程入口，扫描可运行 run 并执行 LangGraph + OpenAI Agents SDK runtime。 | worker 服务启动时运行；本地可通过 Compose workers profile 启动。 | `python -m production_backend.scripts.run_agent_worker`; 本地推荐 `make backend-local-workers` |
| `run_outbox_worker.py` | 独立 outbox worker 进程入口，处理持久副作用任务、重试和 action apply。 | worker 服务启动时运行；本地可通过 Compose workers profile 启动。 | `python -m production_backend.scripts.run_outbox_worker`; 本地推荐 `make backend-local-workers` |
| `worker_runtime.py` | worker 共享运行时工具，负责 stop signal 和 sleep 控制。 | 不单独启动；被 `run_agent_worker.py` 和 `run_outbox_worker.py` import。 | 不直接执行 |

## 回归测试分层

PR 级别通常需要：

- `ruff check app tests scripts`
- `mypy app scripts`
- `pytest production_backend/tests`
- `run_agent_seed_eval.py`
- `check_backup_restore_hooks.py`
- `export_openapi.py` + 快照 diff

有真实依赖服务时需要：

- `check_database_profile.py`
- `check_redis_runtime_controls.py`
- `check_object_storage_profile.py`
- `check_product_asset_storage.py`

定时或发布前需要：

- `run_agent_provider_eval.py`
- `run_agent_replay_eval.py`
- `backend-staging-smoke`
- `backend-production-readiness`

手动运维时使用：

- `inspect_worker_backlog.py`
- `recover_stuck_agent_runs.py`

## 注意事项

- `scripts/` 下的脚本不是服务启动 hook。只有被 CI、Makefile、Compose command
  或人工命令显式调用时才运行。
- `check_object_storage_profile.py` 会创建并删除一个临时诊断对象，可能留下空的
  `.local/object_storage/diagnostics/` 目录；该目录可以删除。
- `check_product_asset_storage.py` 不写入对象存储，只读取 metadata 或对象大小。
- `run_agent_provider_eval.py` 会使用真实模型 provider，运行前需要设置
  `OPENAI_API_KEY`、`OPENAI_MODEL` 和成本预算。
