# Product Backend Scripts

本目录只保留 Product 后端的契约导出、基础设施检查、素材和发布门禁脚本。
Agent worker、记忆、回放、评测和恢复脚本属于独立 Agent Runtime 仓库。

脚本不会随 FastAPI 自动执行；由 CI、Makefile 或人工命令显式调用。需要连接
基础设施的脚本使用 `Settings.from_env()`，执行前加载对应私有 env：

```bash
set -a; . env/compose.local.env; set +a
```

## 推荐入口

| 场景 | 命令 |
|---|---|
| 启动本地 Product API 与基础设施 | `make backend-local-up` |
| 迁移数据库 | `make backend-local-migrate` |
| 检查数据库、Redis、对象存储和 Product 素材 | `make backend-check-infra` |
| 检查静态产品化门禁 | `make backend-productization-status` |
| 运行 Product 边界 smoke | `make backend-smoke` |
| Staging 环境验收 | `make backend-staging-smoke` |
| 导出 OpenAPI 和 API surface | `make backend-export-contracts` |

## 脚本清单

| 脚本 | 用途 |
|---|---|
| `build_product_asset_manifest.py` | 从官方素材目录生成 Product asset manifest。 |
| `check_backup_restore_hooks.py` | 校验 PostgreSQL 与对象存储备份/恢复 hook。 |
| `check_database_profile.py` | 执行数据库连接和 `select 1`。 |
| `check_redis_profile.py` | 对 Product 使用的 Redis 执行通用连接与 `PING`；不检查 Agent runtime 状态。 |
| `check_object_storage_profile.py` | 对对象存储执行临时 `put/get/delete`。 |
| `check_product_asset_storage.py` | 校验 manifest 中 Product 素材的对象键和大小。 |
| `check_productization_status.py` | 校验部署模板、CI、内部 Agent API 边界及旧嵌入式 Runtime 已移除。 |
| `export_openapi.py` | 导出 FastAPI OpenAPI 快照。 |
| `export_api_surface_catalog.py` | 从 OpenAPI 快照生成 API surface 分类目录。 |

## 回归分层

PR 门禁：

- `ruff check app tests scripts`
- `mypy app scripts`
- `pytest tests`
- Alembic head 与空库 SQL 检查
- OpenAPI/API surface 快照 diff
- local/staging Compose config 校验

有真实依赖服务时：

- `check_database_profile.py`
- `check_redis_profile.py`
- `check_object_storage_profile.py`
- `check_product_asset_storage.py`

`check_object_storage_profile.py` 会创建并删除一个临时诊断对象；
`check_product_asset_storage.py` 只读对象 metadata。
