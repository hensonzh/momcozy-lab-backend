# Production Backend Scripts

本目录放显式执行的开发、回归、验收、运维脚本。`run_agent_worker.py` 和
`run_memory_consolidation.py` 会作为独立 worker 进程入口，其他脚本不会随着
FastAPI API 服务启动自动运行。

## 使用原则

- API 服务启动入口是 `app.main:app`，不会自动遍历或执行
  `scripts/`。
- local/test/prod 的基础设施检查通过 `Makefile` 目标显式触发。
- 真实 provider eval、test smoke、production readiness 属于发布或定时验收，
  不应混入普通 API 启动流程。
- 脚本默认读取 `Settings.from_env()`，运行前需要加载对应 env 文件。

加载本地 env 的通用方式：

```bash
set -a; . env/compose.local.env.example; set +a
```

项目默认使用项目级 Python：

```bash
.venv/bin/python
```

## 推荐入口

| 场景 | 推荐命令 | 会运行的脚本 |
|---|---|---|
| 本地启动完整后端 | `make backend-local-up` | Compose 启动基础设施，运行 Alembic migration，然后启动 API、agent-worker、memory-worker |
| 本地单独迁移数据库 | `make backend-local-migrate` | Alembic migration，不走本目录脚本 |
| 本地单独启动/重启 worker | `make backend-local-workers` | `run_agent_worker.py`, `run_memory_consolidation.py` |
| 基础设施验收 | `make backend-check-infra` | database / Redis / object storage / product asset checks |
| 后端产品化门禁 | `make backend-productization-status` | `check_productization_status.py` |
| 后端 smoke | `make backend-smoke` | productization status + seed eval + fact extraction eval |
| 设备开箱决策 provider-live 评测 | `make backend-agent-device-decision-eval` | 使用当前 provider、系统提示词、skill、workflow context 和工具 schema 验证继续/求助决策 |
| Server test 数据重置 | `make backend-test-reset` | 停止 server-test Compose 并删除其 Postgres、Redis、MinIO volumes；仅用于可丢弃数据环境 |
| Server test smoke | `make backend-test-smoke` | productization status + infra checks |
| Production readiness | `make backend-prod-readiness` | productization status + infra checks |

## 脚本清单

| 脚本 | 用途 | 启动时间 | 启动方式 |
|---|---|---|---|
| `build_product_asset_manifest.py` | 根据本地素材目录生成 `product-assets.manifest.json`，用于产品素材发布流程。 | 新增、删除或重命名官方产品素材时手动运行。 | `python scripts/build_product_asset_manifest.py --source-root <asset-dir> --output assets/product-assets.manifest.json --object-key-prefix product-assets/device-guidance/assets` |
| `publish_pump_models_reference.py` | 严格校验吸奶器型号 Markdown，并上传到智能体固定对象键。 | 型号、价格或产品事实更新后运行。 | `python scripts/publish_pump_models_reference.py --source assets/agent-references/pump-models.md` |
| `check_pump_models_reference.py` | 检查对象存储中的吸奶器型号文档存在且可通过运行时 Schema 校验。 | 发布后、环境检查和上线前运行。 | `python scripts/check_pump_models_reference.py` |
| `check_backup_restore_hooks.py` | 检查 PostgreSQL 和对象存储的备份/恢复 hook 配置是否完整。 | PR CI、发布前、生产运维检查时运行。 | `python scripts/check_backup_restore_hooks.py`; 生产严格检查用 `--strict` |
| `check_database_profile.py` | 检查当前 env 下 PostgreSQL 是否可连接。 | `backend-check-infra`、test smoke、production readiness。 | `set -a; . <env-file>; set +a; python scripts/check_database_profile.py` |
| `check_object_storage_profile.py` | 对当前对象存储执行临时 `put/get/delete`，验证 provider 可用。 | `backend-check-infra`、CI MinIO 集成测试、test/prod readiness。 | `python scripts/check_object_storage_profile.py` |
| `check_product_asset_storage.py` | 校验 manifest 里的每个 `object_key` 是否存在于对象存储，且大小等于 `size_bytes`。 | 本地素材同步后、test/prod 素材发布后、`backend-check-infra`。 | `python scripts/check_product_asset_storage.py` |
| `check_productization_status.py` | 检查产品化门禁：必备文档、env profile、CI gate、worker 脚本、Makefile target、旧 runtime 扫描等。 | 每次 PR、本地 smoke、发布前。 | `python scripts/check_productization_status.py`; 机器读取用 `--json` |
| `check_redis_runtime_controls.py` | 检查 Redis lock、cancel flag、stream cursor 等 agent runtime 控制能力。 | `backend-check-infra`、CI Redis 集成测试、staging/production readiness。 | `python scripts/check_redis_runtime_controls.py` |
| `export_openapi.py` | 导出当前 FastAPI OpenAPI schema，用于契约快照比对。 | API 契约变化时手动更新；CI 中生成临时文件并和 docs 快照 diff。 | `python scripts/export_openapi.py --output docs/openapi.generated.json` |
| `export_api_surface_catalog.py` | 从 OpenAPI 扩展字段导出 App/API/运维/内部接口分类目录。 | API surface 变化时，在导出 OpenAPI 后同步更新。 | `python scripts/export_api_surface_catalog.py --openapi-input docs/openapi.generated.json --output docs/api-surface-catalog.md` |
| `inspect_worker_backlog.py` | 只读查看 agent run 和 fact extraction backlog 数量，不修改状态。 | 本地/线上排查 worker 堆积、发布后观察。 | `set -a; . <env-file>; set +a; python scripts/inspect_worker_backlog.py` |
| `recover_stuck_agent_runs.py` | 查找长时间停留在 `running` 的 agent run；默认 dry run，`--apply` 后标记失败并清理 Redis 控制状态。 | 运维恢复卡住的 run 时手动执行。 | `python scripts/recover_stuck_agent_runs.py --limit 20`; 真正修改用 `--apply` |
| `run_agent_replay_eval.py` | 用已保存的 replay bundle 对单个 seed case 做回放断言。 | 线上问题复盘、事故回归、专题修复验证。 | `python scripts/run_agent_replay_eval.py --replay <bundle.json> --suite <suite> --name <case-name>` |
| `run_agent_seed_eval.py` | 对已捕获的真实 runtime/provider trace 执行历史 seed 断言，可输出 JSON/JUnit；缺少 observed trace 时会 fail closed。 | 线上问题复盘、provider-live/nightly 回放，不作为无 trace 的 PR gate。 | `python scripts/run_agent_seed_eval.py --trace-fixtures <observed-traces.json> --output /tmp/agent-seed-eval.json --junit-output /tmp/agent-seed-eval.junit.xml` |
| `run_agent_fact_eval.py` | 通过真实 `AgentFactExtractor` 与确定性 scripted backend 运行聊天事实提取 eval，可输出 JSON/JUnit。 | 每次 PR CI、本地 smoke、改动事实目录/提取规则时。 | `python scripts/run_agent_fact_eval.py --output /tmp/agent-fact-eval.json --junit-output /tmp/agent-fact-eval.junit.xml` |
| `run_device_unboxing_decision_eval.py` | 使用当前真实模型 provider，以及生产系统提示词、设备 skill、workflow context 和工具 schema，验证“完整展示后继续”“遇到问题”“步骤未完整展示却说继续”三类决策；不使用 scripted backend 预设工具调用。 | provider-live/nightly 或设备开箱提示词、skill、上下文投影变化后手动运行。 | `make backend-agent-device-decision-eval`，或 `python scripts/run_device_unboxing_decision_eval.py --output /tmp/device-unboxing-decision-eval.json --trace-output /tmp/device-unboxing-decision-traces.json` |
| `run_agent_worker.py` | 独立 agent run worker 进程入口，扫描可运行 run 并执行原生 OpenAI Responses runtime。 | `make backend-local-up` 或单独 worker 服务启动时运行。 | `python -m scripts.run_agent_worker`; 本地完整启动推荐 `make backend-local-up`，单独重启推荐 `make backend-local-workers` |
| `run_memory_consolidation.py` | 独立夜间记忆 worker；读取前一日本地自然日的已完成对话，幂等更新长期记忆与 bounded snapshot。 | 启动时补跑一次，此后按配置小时运行；也可手工 backfill。 | `python -m scripts.run_memory_consolidation`; 单次补跑用 `--once --date YYYY-MM-DD` |
| `worker_runtime.py` | worker 共享运行时工具，负责 stop signal 和 sleep 控制。 | 不单独启动；被两个 worker 入口 import。 | 不直接执行 |

## 回归测试分层

PR 级别通常需要：

- `ruff check app tests scripts`
- `mypy app scripts`
- `pytest tests`
- `pytest -q tests/test_agent_task8_observed_eval.py`
- `run_agent_fact_eval.py`
- `check_backup_restore_hooks.py`
- `export_openapi.py` + 快照 diff

有真实依赖服务时需要：

- `check_database_profile.py`
- `check_redis_runtime_controls.py`
- `check_object_storage_profile.py`
- `check_product_asset_storage.py`

定时或发布前需要：

- `run_agent_seed_eval.py --trace-fixtures <observed-traces.json>`
- `run_agent_replay_eval.py`
- `make backend-agent-device-decision-eval`（provider-live，不纳入普通 smoke）
- `backend-test-smoke`
- `backend-prod-readiness`

手动运维时使用：

- `inspect_worker_backlog.py`
- `recover_stuck_agent_runs.py`

## 注意事项

- `scripts/` 下的脚本不是服务启动 hook。只有被 CI、Makefile、Compose command
  或人工命令显式调用时才运行。
- `check_object_storage_profile.py` 会创建并删除一个临时诊断对象，可能留下空的
  `.local/object_storage/diagnostics/` 目录；该目录可以删除。
- `check_product_asset_storage.py` 不写入对象存储，只读取 metadata 或对象大小。
