# Momcozy Lab 三仓部署与构建历史审计（截至 2026-09-04）

> **归档说明（2026-09-23）：** 本文记录截至 2026-09-04 的架构设计、服务器审计和首次 test 发布过程，不再作为当前部署操作手册。当前执行依据为 `agent/docs/deployment.md`、`backend/docs/deployment-runbook.md` 和 `app/docs/flutter/release-gate.md`。
>
> 已知过期内容包括旧的 `TEST_APPROVAL_ISSUE` 二次确认机制、CI 镜像 tar 传递流程、当时的迁移 head、服务器资源数量和历史 App build。文中的三仓、双后端、独立数据边界和 test 环境总体原则仍可作为历史背景。

> 设计日期：2026-08-19（Asia/Shanghai）
>
> 服务器全量复核：2026-08-20 20:04；监听/Compose 复核：2026-08-24 17:35；
> 双域名 DNS/TLS 配置复核：2026-08-25
> （Asia/Shanghai，只读审计）
>
> 环境命名迁移更新：2026-09-03；当前源码统一以 `test` 表达共享测试环境。
> 2026-09-04 已完成受控切换和首次 test 发布；历史 App build 56、旧服务器栈及其
> legacy `staging` 标识仍保留用于兼容和审计，不能据此混用两套发布入口。
>
> 适用项目：`app/`、`agent/`、`backend/`
>
> 参考实现：`/Users/lute/project/momcozy/` 的不可变 release、迁移前备份、原子
> current 指针、后端/App 并行 lane、预构建 APK 发布与在线制品校验流程。

## 1. 结论

采用“三仓、双后端、一个联合发布编排层”的方案：

1. `backend/` 固定称为 Product Backend（backend/），独立镜像、数据库、Redis DB 0/key
   前缀、bucket、迁移和回滚；其 test Compose 是共享基础设施唯一所有者。
2. `agent/` 固定称为 Agent Runtime（agent/），独立镜像、数据库、Redis DB 1/key 前缀、
   bucket、运行 worker、迁移和回滚；它不再启动第二套 PostgreSQL/Redis/MinIO。
3. `app/` 同时编译 Product Backend 与 Agent Runtime 两个 HTTPS base URL，不通过路径
   猜测服务归属。
4. 当前不新增第四个 `ops/` 仓库。两个服务各自维护 release manifest；受保护的 App
   test workflow 作为最小 join 编排层，消费两个 manifest 中的 commit、image
   digest 和 OpenAPI hash，并把它们写入 App manifest。需要跨环境推广时再抽出独立 ops 层。
5. 联合发布的顺序是 Product Backend -> Agent Runtime -> 联合端到端验证 -> 发布已预构建 App；App
   本地构建可以与后端部署并行，但 APK 发布必须等待两个后端都通过 join barrier。
6. 所有可部署制品来自干净 commit；release 目录、镜像 tag、App build number 和
   GitHub Release asset 均不可覆盖复用。
7. 新栈与服务器现有 `/opt/momcozy` 栈并行运行：不复用其端口、Compose project、
   Docker network、镜像名、发布目录、App tag 或 Pages 根 manifest。

这不是把参考项目的单一后端 Compose 复制三份。参考项目把产品业务、Agent Runtime worker、
outbox 和 memory worker 放在一个后端镜像中；当前项目已经把 Product Backend 和 Agent Runtime
拆成两个安全与数据边界，发布流程也必须保持该边界。

### 1.1 `staging` 到 `test` 的受控切换

本次源码迁移统一以下目标标识：

- `docker-compose.test.yml`、`env/compose.test.env`；
- Compose project/network：`momcozy-lab-backend-test`、
  `momcozy-lab-agent-test`、`momcozy-lab-test`；
- PostgreSQL：`momcozy_test`、`agent_runtime_test`；Redis 继续使用 logical DB 0/1
  和各自 ACL 前缀；MinIO bucket：`momcozy-test`、`agent-runtime-test`；
- release manifest `environment=test`，GitHub workflow/environment/variables/secrets
  和共享锁统一使用 `test` / `TEST_*`；
- App 继续使用 `unified` flavor/applicationId/Release tag/Pages namespace，只把 runtime
  metadata 和未来 APK 文件名改为 `test`。

这是命名与隔离边界的切换，不是自动数据迁移。当前发布脚本不会把服务器上 legacy
`momcozy-lab-*-staging` volume、数据库或 bucket 静默解释为新 `test` 资源；8001/8002
仍被 legacy 容器占用时，碰撞门禁会拒绝启动。正式切换必须先备份并验证恢复，再明确选择：

1. 测试数据可丢弃：经单独授权停止 legacy 新栈，以空 `test` 数据资源 bootstrap；或
2. 测试数据需保留：离线迁移 PostgreSQL、Redis 和 MinIO 数据并验证计数/对象校验和后，
   才切换 Compose owner、Nginx 与 current manifest。

两种路径都必须先配置 GitHub `test` environment、`TEST_APPROVERS`、
`TEST_APPROVAL_ISSUE` 和 `TEST_*` secrets，并准备服务器私有 `deploy.env`。证书文件路径可
迁移为 `/etc/nginx/tls/momcozy-lab-test`，但本次不轮换 CA 密钥或证书字节；为兼容已安装
build 56，其 CA subject 仍可保留 legacy `MomCozy Staging Internal CA`。历史 Release、
provenance 和 `momcozy-unified-android-staging-1.0.0-56.apk` 永不重写；当前 test 构建
使用 `momcozy-unified-android-test-...apk`。

本次切换已按路径 1（丢弃 legacy 新栈测试数据、从空资源 bootstrap）完成；下面两条
路径保留为未来环境重建或迁移时的约束。

## 2. 当前事实与设计约束

| 项目 | 当前能力 | 需要补齐 |
| --- | --- | --- |
| `backend/` | FastAPI、OpenAPI、Alembic、smoke、共享 test 基础设施、CI 单次构建/测试后推送 GHCR、digest-only deploy/backup/migrate/readiness/rollback、原子 current/previous 与 release manifest | 首次服务器 bootstrap、受保护 environment secrets、真实部署与 restore drill |
| `agent/` | Run worker、独立账本、Behavior Eval、Runtime v1 harness、Product contract gate、CI 不可变镜像、digest-only deploy/backup/migrate/readiness/rollback、跨服务 release identity | test edge 实际安装、真实队列 drain/恢复演练、首次部署与 restore drill |
| `app/` | local/unified/production flavor、test runtime metadata、双 base URL、完整 CI、签名强制、live join barrier、预构建 APK 复用、不可覆盖 Release、`/unified/` Pages、联合 release metadata | 配置 GitHub secrets、正式签名发布、真机/BLE/MotionPose 验证 |

必须保留的边界：

- Product Backend 只持有 JWT 私钥；Agent Runtime 通过 Product Backend JWKS 验证 App access token。
- `AGENT_RUNTIME_SERVICE_API_KEY` 只用于 Agent Runtime -> Product Backend 的内部调用，
  不与 Product Backend 运维 key 或 Agent Runtime admin key 复用。
- Product Backend 和 Agent Runtime 不共享 PostgreSQL schema，也不互相导入实现代码。
- Agent Runtime 只通过 typed `/v1/internal/agent/*` API 读取或写入业务事实。
- App 使用 `MOMCOZY_API_BASE_URL` 访问 Product Backend，使用
  `MOMCOZY_AGENT_API_BASE_URL` 访问 `/v1/agent/*`。
- App 只消费 Agent Runtime 应用事件；不能从 assistant 文本或原始 Tool output 推导权威状态。

### 2.1 服务器碰撞审计快照

2026-08-19 初次盘点、2026-08-20 全量复核，并于 2026-08-24 复核运行身份和监听
`ubuntu@lute-momcozylab.luteos.cloud` 的只读结果：

| 资源 | 服务器现状 | 新栈决定 |
| --- | --- | --- |
| 旧 release root | `/opt/momcozy`，current=`30fbe2f5...` | 使用 `/opt/momcozy-lab`，禁止读写旧 root |
| 旧 Compose project | `momcozy-test-backend`，7 个常驻运行容器；另有 1 个正常退出的一次性 `minio-init` | 使用 `momcozy-lab-*` 前缀 |
| 旧 Docker network | `momcozy-test-backend_default` | 使用 `momcozy-lab-test` |
| 旧镜像 | `momcozy-production-backend:*` | 使用 `momcozy-lab-backend:*`、`momcozy-lab-agent:*` |
| 旧内部 API bind | `127.0.0.1:8000` | 保留不动；新 Product Backend/Agent Runtime 使用空闲的 8001/8002 |
| 公网 TLS | Nginx 占用且安全组只保留 8443 | Product Backend/Agent Runtime 以两个新域名在同一 8443 listener 上做 SNI 分流 |
| 旧 Nginx site | `momcozy-api`，upstream=`127.0.0.1:8000` | 新建两个独立 SNI site，禁止改旧 upstream |
| 旧 TLS 运维 | `momcozy-tls-renew.timer` 正常运行 | 两个新站点复用一张双 SAN leaf 和同一内部 CA，不再建第二套 CA |
| 旧 App 发布 | `android-v1.0.0-55` 与 Pages 根 manifest 已发布 | 使用 `unified-*` tag/asset 和 `/unified/` Pages 子路径 |
| 旧 staging 包名 | `com.momcozymai.app.flutterpoc.staging` | 并行期使用 `.unified` 包名，避免覆盖/签名冲突 |

以下 2026-08-24 结果是首次部署前的历史审计快照，不代表 2026-09-03 的在线状态。
当时确认旧栈占用 `127.0.0.1:8000`，`8001/8002` 无 listener；
公网继续只使用 Nginx `8443`，新入口必须通过尚未占用的新 server name 做 SNI 分流。
`/opt/momcozy-lab` 不存在，且没有任何 `momcozy-lab-*` container、network、volume、
image 或 Nginx site；GitHub Releases 也没有 `unified-*` tag，Pages `/unified/` 返回 404。
旧 `momcozy-api` site 的审计基线 SHA-256 为
`4a183183c68a6f85e8669e72550097fd3cbdf0fd42f6c6b803bc6fb6ead7a7c4`，复核后旧栈
readiness 仍为 `ok`。这些结果只证明资源当前可用，并不代表已经预留；首次部署前必须
再次执行同一门禁。

主机当前为 8 vCPU、15 GiB RAM（约 13 GiB available）、30 GiB 可用磁盘；旧栈
容器当前合计低于 1 GiB 常驻内存。容量允许进行隔离 test，但新 Compose 必须设置
CPU/内存上限，首次真实 Agent Runtime 并发前仍需压测。服务器已有 8.42 GiB Docker image 和
3.28 GiB build cache；清理只能按保留 current/previous/失败诊断制品的策略执行，不能
在发布脚本中无条件 prune。

初始共存预算建议如下，使用 Compose `cpus`/`mem_limit` 实际约束，不只写
`deploy.resources`：

| 新服务 | CPU 上限 | 内存上限 |
| --- | ---: | ---: |
| Product Backend · API process | 1.0 | 1 GiB |
| 共享 PostgreSQL | 1.0 | 1.5 GiB |
| 共享 Redis | 0.25 | 256 MiB |
| Agent Runtime · API process | 0.5 | 768 MiB |
| Agent Runtime · worker process | 2.0 | 3 GiB |
| 共享 MinIO/对象存储 | 0.5 | 1 GiB |

Agent Runtime test 初始 `AGENT_WORKER_CONCURRENCY=2`，经 DB pool、模型 RPM/TPM、首事件延迟
和内存压测后再增加。新栈不得加入 `momcozy-test-backend_default`，不得挂载旧
`momcozy-test-backend_*` volumes，也不得复用旧 PostgreSQL/Redis/MinIO 作为自己的
权威状态。

### 2.2 当前服务与发布来源

主机是 AWS EC2 `c5a.2xlarge`，位于 `ap-southeast-1c`，公网地址
`54.254.112.41`；安全组为 `launch-wizard-3`（`sg-0e7b27136e3571110`）。主机内
UFW 未启用，云安全组规则无法由当前实例角色读取。Ubuntu 24.04 已持续运行 37 天，
Docker 29.1.3、Compose 2.40.3、Nginx 1.24.0 均正常。

当前 Compose 拓扑：

| 服务 | 镜像/命令 | 对外绑定 | 状态 |
| --- | --- | --- | --- |
| `api` | `momcozy-production-backend:30fbe2f` / Uvicorn | `127.0.0.1:8000 -> 8000` | healthy |
| `agent-worker` | 同一后端镜像 / `scripts.run_agent_worker` | 无 | running，0 restart |
| `outbox-worker` | 同一后端镜像 / `scripts.run_outbox_worker` | 无 | running，0 restart |
| `memory-worker` | 同一后端镜像 / `scripts.run_memory_consolidation` | 无 | running，0 restart |
| `postgres` | `postgres:16` | 仅 Compose 网络 `5432` | healthy，0 restart |
| `redis` | `redis:7` | 仅 Compose 网络 `6379` | healthy，0 restart |
| `minio` | `minio/minio:latest` | 仅 Compose 网络 `9000` | running，0 restart |
| `minio-init` | `minio/mc:latest` | 无 | 一次性初始化已成功退出 |

应用容器来自当前 release
`/opt/momcozy/releases/30fbe2f59a92856086dbe9fcef2c6b49a8231562`；
`/opt/momcozy/current` 指向该目录。PostgreSQL、Redis、MinIO 则仍是较早的
`5318cd9f.../production_backend/docker-compose.staging.yml` 创建的长驻容器，继续使用同一
Compose project、network 和三个 named volumes。这是发布时只替换 API/workers、保留
状态服务的结果，不是第二套服务抢占端口；但所有运维命令必须按 service label/manifest
解析 owner，不能假定一个 Compose 文件能完整描述当前所有容器来源。

运行验证结果：

- 本机与公网 `live/ready` 均为 200，数据库和 Redis 为 `ok`；Alembic 为
  `20260811_0063 (head)`。
- 4 个应用进程最近 24 小时日志的 traceback/fatal/unhandled/error 标记计数均为 0。
- API 实际安全配置为 `APP_ENV=staging`、OpenAI `gpt-realtime-2.1`、视觉上下文启用，
  provider credential 存在；审计未读取或输出 credential 值。
- 2026-08-24 API 镜像/release 已是 `30fbe2f`；此前 OpenAPI `info.version` 和容器
  `APP_VERSION` 仍为 `ad31793`，当前版本标识不可用于判断真实部署 commit。
- 服务器保留 63 个 release（524 MiB）、42 个 backup entry（26 MiB）、72 个 Docker
  image（8.42 GiB）和 3.28 GiB build cache；磁盘仍有 30 GiB，但发布流程需要显式且
  owner-safe 的保留策略。

### 2.3 当前 Nginx 有效配置

主机仅监听公网 `80`、`8443` 和 SSH `22`；应用端口 `8000` 只绑定 loopback，
PostgreSQL、Redis、MinIO 没有 host/public bind。`nginx -t` 语法成功，当前启用三个
site：

| 入口 | server name | 行为/上游 | TLS |
| --- | --- | --- | --- |
| `:80` | `cozyai`、`cozyai.luteos.cloud`、`cozyai.54-254-112-41.sslip.io` | 308 到同 host 的 `:8443` | 无 |
| `:80` | `flow`、`flow.54-254-112-41.sslip.io`、`54.254.112.41` | 静态目录 `/var/www/flow` | 无 |
| `:8443` default | `_` | 未知 Host 返回 444 | MomCozy internal-CA leaf |
| `:8443` | `cozyai*` | `/` 302 到 `/prototype/`，静态目录 `/var/www/cozyai/current` | 自签 leaf，有效至 2027-07-31 |
| `:8443` | `lute-momcozylab.luteos.cloud` | 反代 `127.0.0.1:8000` | internal-CA leaf，有效至 2027-08-16 |

MomCozy API site 的特殊规则：

- `/v1/realtime-voice-session` 保留 WebSocket upgrade，读写超时 3600 秒。
- `/v1/files/upload` 禁用 request/response buffering，超时 120 秒，最大 body 16 MiB。
- 其他 API 禁用 proxy buffering/cache，读写超时 3600 秒，并传递
  `X-Real-IP`、`X-Forwarded-*` 和 `X-Request-ID`。
- 精确 `/app`、`/app/` 302 到 GitHub Pages；`/app/releases/*.apk` 和其他
  `/app/*` 仍可从 `/var/www/momcozy/android-apk` 提供兼容静态文件。
- 服务器本地 manifest 仍是 `1.0.0+8`，而主入口跳转的 GitHub Pages 已发布
  `1.0.0+55`；本地目录现在是旧的兼容副本，不应再作为当前版本事实源。

TLS leaf 每日由 `momcozy-tls-renew.timer` 检查，在剩余 30 天时用服务器上的内部 CA
轮换，替换前备份、执行 `nginx -t`、reload，并以 CA 访问公网健康端点验证；2026-08-20
最近一次检查成功且判定无需续期。私钥均为 root `0600`。

### 2.4 当前运维问题与对新方案的约束

1. `nginx -t` 会报告 `protocol options redefined`：同一 `8443` address 的 default
   server 与其他 server 对 `http2` 选项定义不一致。配置可运行，但新增 site 前应先统一
   listener 参数，避免继续叠加告警。
2. 从公网访问 `:80` 超时；主机监听正常且 UFW inactive，因此云安全组/NACL 是首要
   排查对象。当前 HTTP -> HTTPS redirect 实际不可用，`flow` 也无法公网访问。安全组
   只保留 `8443`；新栈必须使用新域名 + SNI，共用现有公网 listener，不能新增公网端口。
3. `cozyai.luteos.cloud` 当前没有可解析的 DNS 记录，只有 sslip.io 名称可用；Nginx 中
   该 server name 目前是死别名。
4. 全局 Nginx 配置仍声明 TLS 1.0/1.1；三个现有 TLS server 都局部收紧为 1.2/1.3，
   当前入口未实际启用旧协议，但全局默认应同步收紧以免未来 site 漏配。
5. 内部 CA 私钥与 leaf 部署在同一应用主机。它仅适合隔离 test；生产环境应改用
   受信任 CA 或把签发 CA 私钥移出应用服务器。
6. 旧 test Compose 内含固定 PostgreSQL/MinIO 默认凭证，并使用未固定 digest 的
   `minio/minio:latest`、`minio/mc:latest`。这些端口当前未对外开放，但新栈不得复制该
   secret/image 管理方式。
7. API/PostgreSQL/Redis 有 healthcheck；三个 worker 和 MinIO 没有容器 healthcheck，
   worker 目前只能证明进程存活。新 Agent Runtime 发布必须用 heartbeat、queue age 和 stuck-run
   recovery 做 readiness/运维门禁。
8. `/opt/momcozy/shared/compose.test.env` 为 root `0600`，但 release/image/build cache
   会持续累积；任何清理都必须保留 current、previous、失败诊断制品和可恢复备份，禁止
   无条件 `docker system prune`。

## 3. 环境与网络拓扑

### 3.1 推荐 test 拓扑

第一阶段继续使用现有单机 test，但为两个服务使用不同 origin：

| 用途 | 地址 |
| --- | --- |
| Product Backend public | `https://backend-test.lute-momcozylab.luteos.cloud:8443` |
| Agent Runtime public | `https://agent-test.lute-momcozylab.luteos.cloud:8443` |
| Product Backend host bind | `127.0.0.1:8001` |
| Agent Runtime host bind | `127.0.0.1:8002` |
| Agent Runtime -> Product Backend | `http://product-backend:8000`，经私有 Docker network/DNS |

```text
Flutter test App
  |-- Product Backend SNI :8443 --> Nginx Product Backend -> 127.0.0.1:8001
  |                                      |-- PostgreSQL / momcozy_test
  |                                      |-- Redis DB 0 / Product Backend prefixes
  |                                      `-- MinIO / momcozy-test
  |
  `-- Agent Runtime SNI/SSE :8443 -> Nginx Agent Runtime -> 127.0.0.1:8002
                                             |-- PostgreSQL / agent_runtime_test
                                             |-- Redis DB 1 / agent-runtime:*
                                             |-- MinIO / agent-runtime-test
                                             `-- Agent Runtime run worker
                                                      |
                                                      `-- private product-backend:8000
```

Product Backend Compose 创建并拥有 network `momcozy-lab-test` 以及唯一一套
PostgreSQL、Redis、MinIO；Agent Runtime Compose 把该网络声明为 external consumer。
Product Backend 使用稳定
alias `product-backend`；基础设施使用 `test-postgres`、`test-redis`、
`test-minio`。数据库、Redis 和对象存储端口不映射到主机或公网。

Nginx 分工：

- Product Backend edge 保留上传限制、WebSocket、model capability URL 日志关闭和
  request timeout 规则；版本化模板为
  `backend/deploy/nginx/momcozy-lab-product-backend.conf`。
- Agent Runtime edge 代理 Agent Runtime API 和 health；SSE 必须关闭
  buffering/cache/compression，保持
  `Authorization` header，并设置长 `proxy_read_timeout`。
- Agent Runtime 版本化模板为
  `agent/deploy/nginx/momcozy-lab-agent-runtime.conf`。
- 两个服务文件使用不同 `server_name` 在同一 `8443` listener 上由 SNI 分流，并转发
  `X-Request-ID`；应用容器只绑定 loopback 或私有网络。
- 宿主机单独维护且只维护一个 unknown-host/default rejection site；各服务配置文件不
  声明 `default_server`，宿主机启用文件固定命名为
  `momcozy-lab-product-backend` 和 `momcozy-lab-agent-runtime`。
- 不修改现有 `/etc/nginx/sites-enabled/momcozy-api`，也不改其既有 server name、
  `momcozy_api` upstream 或 `/var/www/momcozy/android-apk`。

### 3.2 TLS

目标 App 已移除 2026-08-15 过期且只覆盖旧域名的 leaf，并改为捆绑
`assets/certificates/momcozy-test-internal-ca.pem`。该 CA 有效至 2036-08-13，
App 仍保留主机名、有效期和证书链校验。

服务器旧入口当前 leaf 由 `MomCozy Staging Internal CA` 签发，有效至 2027-08-16，
但 SAN 只包含旧域名。新栈必须在
`/etc/nginx/tls/momcozy-lab-test/{fullchain.pem,privkey.pem}` 安装由同一 CA 签发、
同时覆盖以下两个 SAN 的新 leaf：

- `backend-test.lute-momcozylab.luteos.cloud`
- `agent-test.lute-momcozylab.luteos.cloud`

两个域名当前都解析到 `54.254.112.41`。在新 Nginx site 尚未启用前，它们仍会命中旧
default site，返回只覆盖旧域名的证书，因此不能视为 TLS 配置已完成。

发布前必须完成以下之一：

1. 推荐：入口使用受信任 CA 证书，逐步移除 App 专用 test CA。
2. 当前闭网 test：复用服务器已有长期内部 CA 体系；App 只捆绑 CA 证书，Nginx
   在 `8443` 为两个 SNI 域名提供短期双 SAN leaf，并将新证书纳入剩余 30 天前轮换。
   不得
   把 leaf 或 CA 私钥打入 App/镜像。

每次预检同时验证两个 SNI 域名的链、主机名和剩余有效期。App 安装全局
SecurityContext 后用同一 CA 分别验证 Product Backend/Agent Runtime `:8443`，并增加两个 endpoint
的测试和制品验证。云安全组继续只允许 `8443`；新 site 建立后还必须从公网构建机执行
两个域名的 SNI/Host 连通性测试，且入口只开放给批准的来源范围。

### 3.3 local 与 test

| 环境 | Product Backend | Agent Runtime | App 制品 |
| --- | --- | --- | --- |
| local | `http://127.0.0.1:8769` | `http://127.0.0.1:8010` | local debug，不发布 |
| test | `https://backend-test.lute-momcozylab.luteos.cloud:8443` | `https://agent-test.lute-momcozylab.luteos.cloud:8443` | unified test release APK，隔离内测分发 |

当前先移除 Product Backend/Agent Runtime production Compose 与 env 模板；代码层的 production 安全
校验和 Flutter production flavor 保留，待真实生产目标、托管依赖和发布审批确定后再设计。

`backend/docker-compose.local.yml` 当前暴露 8000，而 App 默认 Product Backend 端口是 8769；
落地时应把 Product Backend 本地 host bind 统一为 `8769:8000`，并同步 Agent Runtime local 的
`PRODUCT_BACKEND_BASE_URL`/`AUTH_JWKS_URL`，消除双默认值。

## 4. 服务器目录、镜像和发布身份

建议目录：

```text
/opt/momcozy-lab/
  releases/
    backend/<full-backend-sha>/
    agent/<full-agent-sha>/
  current/
    backend -> ../releases/backend/<sha>
    agent   -> ../releases/agent/<sha>
  shared/
    backend/deploy.env
    agent/deploy.env
    platform.env
  backups/
    backend/
    agent/
  manifests/<release-id>.json
```

制品命名：

| 制品 | 约定 |
| --- | --- |
| Product Backend image | `momcozy-lab-backend:<backend-sha7>`，最终记录 digest |
| Agent Runtime image | `momcozy-lab-agent:<agent-sha7>`，最终记录 digest |
| Product Backend release dir | 完整 40 位 commit SHA；存在时只允许校验/复用，不覆盖 |
| Agent Runtime release dir | 完整 40 位 commit SHA；存在时只允许校验/复用，不覆盖 |
| Android tag | `unified-android-v<version-name>-<build-number>`，build number 永不复用 |
| APK | `momcozy-unified-android-test-<version-name>-<build-number>.apk` |
| Pages | `https://hensonzh.github.io/momcozy-lab-releases/unified/` |

Compose project 名必须稳定且互不冲突，例如：

- `momcozy-lab-backend-test`
- `momcozy-lab-agent-test`

基础设施属于 `momcozy-lab-backend-test`，不再维护第三个 infra Compose project。

验证脚本使用 `docker compose -p ... ps -q <service>` 获取容器，不依赖易漂移的硬编码
container name。

## 5. 联合编排边界（当前实现）

当前规模不新增第四个 `ops/` 仓库。职责按所有权留在三个仓库中：

- `backend/` 的 CI 只构建一次 Product Backend 镜像；全部门禁通过后把同一镜像推送到
  GHCR，并输出 commit、digest 清单。受保护的 test workflow 只消费 digest，不在
  服务器重新构建。
- `agent/` 使用相同模式，并在部署前验证当前 Product Backend release manifest 和冻结
  OpenAPI 合同。
- `app/` 的受保护 test workflow 是最小联合编排层：读取服务器当前两个 release
  manifest，验证 dispatch identity、冻结合同和 live OpenAPI，执行双服务 join smoke，
  再发布同一次 release gate 已构建的 APK。
- 三个 workflow 共用 GitHub `test` environment 的审批与 secrets，但各自使用独立
  concurrency group，服务发布目录、current/previous 指针和 release manifest 仍由各自
  仓库维护。

该实现避免为单一 test 环境维护一套重复的中央脚本。以后出现 production、多区域、
多集群或需要一次审批原子编排多个环境时，再把“选择两个服务 manifest 并推广”的薄层
抽成独立 ops 仓库；业务构建、迁移和验证逻辑仍留在原仓库。

真实 env、签名材料、GitHub token、SSH key、JWT 私钥、服务 key 和 provider key 不进入
release 目录或 manifest，只进入服务器 mode `0600` 私有 env、secret manager 或受保护
CI environment。

## 6. 契约与版本传播

每个联合发布形成一条可追溯链：

```text
backend commit
  -> Product Backend image digest + Product Backend migration head + Product Backend OpenAPI SHA256
  -> agent 构建/CI 校验该精确 Product Backend OpenAPI
  -> agent commit + Agent Runtime image digest + Agent Runtime migration head + Agent Runtime OpenAPI SHA256
  -> app 构建同时冻结两个 OpenAPI SHA256
  -> APK manifest 记录三个 commit、两个 URL、两个 OpenAPI hash 和 APK SHA256
```

规则：

1. `backend/` 使用 `make backend-export-contracts` 生成 Product Backend OpenAPI；生成后必须
   `git diff --exit-code`。
2. `agent/` 的 `docs/contracts/product.openapi.generated.json` 必须来自准备部署的
   Product Backend artifact，并通过 `scripts/check_product_backend_contract.py --openapi-path ...`。
3. `agent/` 导出自己的 Agent Runtime OpenAPI，CI 校验没有未提交漂移。
4. `app/` 的 Product Backend 与 Agent Runtime 两份冻结快照必须来自上述两个精确 artifact，并通过
   `scripts/validate_backend_contract.py`。
5. App release note/manifest 记录后端版本，不把移动中的 `main` 当兼容边界。

兼容发布顺序：

- 后端兼容新增：先 Product Backend，后 Agent Runtime，再 App。
- Agent Runtime-only：验证当前 Product Backend artifact 后只部署 Agent Runtime。
- App-only：验证当前线上 Product Backend/Agent Runtime contract 与 readiness 后构建/发布 App。
- breaking change：采用 expand -> dual-read/write/兼容 Agent Runtime -> App -> contract 清理；
  不能在一个发布中删除旧字段，因为旧移动客户端会长期存在。

## 7. CI 门禁

两个服务仓库都把 `docker-compose.ci.yml` 定义为
`docker-compose.local.yml` 的 CI-only override，而不是可独立运行或部署到服务器的
环境。合并后的身份固定为：

| 服务 | Compose project | Image |
| --- | --- | --- |
| Product Backend | `momcozy-lab-backend-ci` | `momcozy-lab-backend:ci` |
| Agent Runtime | `momcozy-lab-agent-ci` | `momcozy-lab-agent:ci` |

Product Backend CI 在 runner 上生成一次性 RSA 私钥；Agent Runtime CI 只使用公开
JWKS fixture 和不会发起真实模型请求的 CI credential。两边的 container job 都必须
验证 Compose merge、非 root image、显式 migration、真实进程 readiness、失败日志，
并在 `always()` 清理 volumes。CI project/image 不得复用 local 或 test 名称。

### 7.1 Product Backend

至少执行：

```bash
.venv/bin/ruff check app tests scripts
.venv/bin/mypy app scripts
.venv/bin/python -m pytest tests
.venv/bin/python -m alembic -c alembic.ini heads
make backend-export-contracts
make backend-productization-status
make backend-smoke
docker compose --env-file env/compose.test.env -f docker-compose.test.yml config --quiet
docker compose -f docker-compose.local.yml -f docker-compose.ci.yml config --quiet
# CI 的 container job 通过 Buildx 构建一次 momcozy-lab-backend:ci，
# 完成 smoke 后保存该镜像；publish job 只 load/tag/push 同一份 tar。
```

PostgreSQL CI 另外执行 `alembic upgrade head` 和 `alembic check`，验证 Product Backend schema
不包含 Runtime 表。

### 7.2 Agent Runtime

至少执行：

```bash
.venv/bin/ruff check app tests scripts migrations
.venv/bin/mypy app tests
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python scripts/run_behavior_eval.py --validate-only
PYTHONPATH=. .venv/bin/python scripts/check_product_backend_contract.py \
  --openapi-path <exact-product-openapi>
git diff --check
docker compose --env-file env/compose.test.env -f docker-compose.test.yml config --quiet
docker compose -f docker-compose.local.yml -f docker-compose.ci.yml config --quiet
# CI 的 container job 通过 Buildx 构建一次 momcozy-lab-agent:ci，
# 完成 contract/eval/smoke 后保存该镜像；publish job 不再次构建。
```

PostgreSQL CI 执行 baseline/后续 migration、`alembic check`，并验证 Runtime schema
不包含 Product Backend 业务表。发布 gate 还要覆盖 Context Pipeline、SDK execution、
deterministic safety、Replay/Behavior Eval 和 worker heartbeat readiness。

### 7.3 Flutter App

至少执行：

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
python3 scripts/validate_backend_contract.py
node scripts/check-flutter-android-packaging.mjs
node scripts/check-flutter-security-privacy.mjs
dart format --output=none --set-exit-if-changed lib test integration_test tool
flutter analyze --no-pub
flutter test --no-pub
```

真实 test smoke 是发布 join barrier，不能以“命令执行但全部 skipped”计为通过。
必须显式设置 `MOMCOZY_TEST_SMOKE=1`；联合发布还需在隔离测试账号上设置
`MOMCOZY_TEST_SMOKE_AGENT=1`，有数据清理方案后才启用 mutation probe。

发布用 test release 必须设置 `MOMCOZY_REQUIRE_RELEASE_SIGNING=1`。缺少 release
keystore 时生成的 debug-signed release APK 只能作为本地 smoke artifact，不能发布。

### 7.4 App 并行安装与发布命名空间

历史隔离前的参考 App 与目标 App 都是 `1.0.0+55`，都使用
`com.momcozymai.app.flutterpoc.staging`，并默认发布到同一个 GitHub repo、同一个
`android-v1.0.0-55` tag 和 Pages 根 manifest。当前脚本还允许 `--clobber`；因此目标
App 在未隔离前禁止发布。

并行验证阶段新增 `unified` Gradle/Flutter flavor：

| 项目 | unified 约定 |
| --- | --- |
| applicationId | `com.momcozymai.app.flutterpoc.unified` |
| app label | `Momcozy Lab Unified` |
| runtime config | `MOMCOZY_ENV=test` |
| Product Backend/Agent Runtime | 同一 8443 listener 上两个 SNI HTTPS origin |
| 首个 build number | 当前已发布 57；后续发布必须递增且不可复用 |
| GitHub tag | `unified-android-v<version>-<build>` |
| asset | `momcozy-unified-android-test-<version>-<build>.apk` |
| Pages | `/unified/index.html`、`/unified/manifest.json`、`/unified/assets/*` |

`flutter-api-config.mjs`、build/release gate、APK builder 和下载页脚本都要显式支持
`unified`；Gradle flavor 与运行环境要分开表达，不能用 `local` flavor 冒充 test。
发布脚本只能写 `unified/` 子目录，不能改 Pages 根 `index.html`、`manifest.json` 或
旧二维码。相同 tag 已存在时，只有 digest 完全一致才允许幂等复验，禁止覆盖上传。

只有旧 staging App 和旧服务器明确退役、且确认使用相同 release signing key 后，
才可以执行有意图的 cutover：恢复 `.staging` 包名、使用高于所有旧包的 build number，
再切换旧发布根入口。该 cutover 不属于并行部署流程。

## 8. 联合发布流程

```text
clean commits + App build number + release manifest draft
                         |
             三仓 gates 并行执行
                         |
       +-----------------+------------------+
       |                                    |
后端制品 lane                         App 构建 lane
CI 产出 Product Backend/Agent Runtime digest  build test release APK
       |                              verify 双 URL/签名/SHA
deploy + verify Product Backend                |
       |                                       |
exact Product Backend contract gate                    |
       |                                       |
deploy + verify Agent Runtime                  |
       +-----------------+---------------------+
                         |
       Product Backend + Agent Runtime + 跨服务 E2E join barrier
                         |
                发布已预构建 APK
                         |
              GitHub Release + Pages 校验
                         |
          封存最终 release manifest（service current pointers 已分别更新）
```

### 8.1 准备

1. 检查三个 repo 的 branch、完整 HEAD、`git diff --check` 和 clean 状态。
2. 运行资源碰撞门禁，确认旧 `momcozy-test-backend`、8443/8000、`/opt/momcozy`、
   Pages 根发布和 `android-v*` tags 仍归旧栈所有；确认新资源不存在或由预期的新栈
   owner 持有。
3. 若发布 App，先递增 `pubspec.yaml` build number 至高于当前 57，确认
   `unified-android-v*` tag 未使用，单独提交 version bump。
4. 记录线上 current release、两个镜像、两个 migration revision、TLS、磁盘、容器状态
   和 env 文件备份目标；只报告 credential 是否存在，不输出值。
5. 创建 release manifest draft。
6. Product Backend、Agent Runtime 的 deploy/rollback 与 App 最终推广都必须持有宿主机
   `/opt/momcozy-lab/shared/test-release.lock`。仓库内 GitHub concurrency 只负责各自
   排队，不能替代这把跨仓库 `flock`；App 获取锁后必须重新读取两份 current manifest。
   App 通过 SSH stdin 绑定的 holder 持有锁：远端在 stdin EOF 时释放 `flock`，本地用
   runner 临时目录的 0600 FIFO 维持 stdin，并在 `EXIT` cleanup 先停止 writer、再回收
   SSH；不得使用固定时长租期。

### 8.2 Product Backend 部署

1. 将精确 backend commit 解包到新的 immutable release dir。
2. 拉取 CI 已验证且由 digest 固定的镜像，核对 OCI revision label，运行 settings
   fail-fast、Compose config 和 OpenAPI hash 校验；服务器禁止重新构建镜像。
3. 对比线上 revision 与新镜像的唯一 Alembic head。
4. revision 不同才做 timestamped PostgreSQL backup，然后显式运行 migration job。
5. 只替换 Product Backend API，不触碰 Agent Runtime/有状态依赖。
6. 验证容器 health、公共 live/ready、JWKS、OpenAPI、对象存储、日志和内部 Agent Runtime API。
7. 只有验证成功后才原子更新 Product Backend release manifest/current pointer；
   私有 env 不保存镜像身份，restart/rollback 必须从 current manifest 派生。

### 8.3 Agent Runtime 部署

1. 先用待部署 Agent Runtime 校验刚上线 Product Backend 的精确 OpenAPI artifact。
2. 将精确 agent commit 解包到新的 immutable release dir，拉取 CI 已验证且由 digest
   固定的镜像，核对 OCI revision label，并运行镜像内可执行的 behavior/contract gate；
   完整 Runtime v1 pytest harness 属于 CI 门禁，生产镜像不携带 tests/pytest。服务器禁止重新构建。
3. 对比 Agent Runtime DB revision；有变化时先备份，再显式运行 migration job。
4. 暂停领取新 Run，等待短任务安全点/租约，替换 Agent Runtime API 和 worker，再恢复领取。
5. 验证 API ready、worker heartbeat、Product Backend JWKS、Agent Runtime -> Product Backend service auth、
   Replay/SSE/cancel、Action confirmation/idempotency、行为 eval 和隐私安全日志。
6. 验证成功后更新 Agent Runtime release manifest/current pointer；私有 env 不保存
   镜像或 heartbeat generation。

Agent Runtime test 的 migration 由显式发布步骤触发；API/worker 普通 restart 不应隐式执行
schema migration。

### 8.4 App 本地构建与发布

App lane 在后端部署期间可以完成：

```bash
MOMCOZY_API_BASE_URL=https://backend-test.lute-momcozylab.luteos.cloud:8443 \
MOMCOZY_AGENT_API_BASE_URL=https://agent-test.lute-momcozylab.luteos.cloud:8443 \
MOMCOZY_APK_FLAVOR=unified \
MOMCOZY_APK_MODE=release \
MOMCOZY_REQUIRE_RELEASE_SIGNING=1 \
MOMCOZY_SKIP_UPLOAD=1 \
./scripts/build-flutter-app.sh
```

本地验证必须证明：

- APK version/build 与 App commit 一致。
- APK 同时包含精确 Product Backend URL 和 Agent Runtime URL。
- flavor=`unified`、runtime environment=`test`、mode=`release`，且为配置的
  release key 签名。
- test CA/系统信任配置正确，TLS leaf 未临近过期。
- APK 与 dist copy SHA256/size 一致，PDF native packaging 正确。
- manifest 记录三个 commit、两个 OpenAPI hash、两个 URL 和 App commit。

只有 Product Backend、Agent Runtime、跨服务 smoke 和本地 APK 四项全部成功，才用
`MOMCOZY_APK_INPUT` 发布既有 APK，禁止重新 build。GitHub Release 同时保存 APK、checksum
和不含时间戳的 provenance JSON；相同 tag 只有三者逐字一致才允许幂等重试，否则直接
失败，不得 `--clobber` 覆盖。发布后验证 namespaced GitHub asset、checksum、
`/unified/manifest.json`、页面版本、APK URL、大小和 SHA256 全部一致，同时断言 Pages
根 manifest 仍指向旧 App 且内容未变。
Pages 必须由管理员预先配置为从 release 仓库 `main` 根目录发布；发布 token 只需
Releases/Contents 权限，脚本不得在发布过程中调用 Pages/Administration API。

### 8.5 强制资源碰撞门禁

首次部署和每次 redeploy 都必须执行 `check_collision_boundaries.sh`。门禁不是简单检查
“端口是否空闲”：首次部署要求资源不存在；redeploy 要求资源由 manifest 中的预期
owner 持有。

| 检查 | 通过条件 |
| --- | --- |
| 旧服务 | `momcozy-test-backend` 仍 healthy，镜像/Compose/current 未被新流程改写 |
| 旧监听 | 8443 的旧 server name 仍由旧 Nginx site 持有；8000 仍指向旧 API |
| 新监听 | 8001/8002 首次为空；8443 上两个新 SNI server name 首次不存在，之后 owner 必须匹配 |
| release root | `/opt/momcozy` 不在任何新命令目标内；只写 `/opt/momcozy-lab` |
| Compose | 所有命令显式 `-p momcozy-lab-...`，禁止裸 `docker compose down/up` |
| network/volume | 名称以 `momcozy-lab-` 开头，label/project 与预期一致 |
| Nginx | 旧 `momcozy-api` 文件 checksum 不变；`nginx -t` 后才 reload |
| TLS | 两个新 SNI 域名均在 8443 用预期 CA 验证，leaf 剩余至少 30 天 |
| GitHub | `unified-android-v*` 未使用；旧 `android-v*` releases 不编辑 |
| Pages | 只改 `unified/`；发布前后根 manifest SHA256 相同 |
| APK | package=`...unified`，双 URL/签名/versionCode 与 manifest 一致 |
| 容量 | 磁盘至少保留 10 GiB，内存/CPU limit 存在，旧栈 readiness 正常 |

任何一项 owner 不符都停止。门禁不得自动删除冲突容器、释放端口、覆盖 Nginx、prune
镜像或改 GitHub asset；这些都需要单独诊断和授权。

## 9. Migration 策略

当前 `backend/` 与 `agent/` 文档都声明 migration 是“空数据库 fresh baseline”，
不支持旧 schema。由此分成两个阶段：

### 9.1 当前 resettable test bootstrap

- 只允许对明确可丢弃的 Product Backend `momcozy_test` 与 Agent Runtime
  `agent_runtime_test` 数据库执行重建。
- 删除/重建前必须另行获得明确授权并记录目标数据库；发布脚本不能自动判断后删除。
- Product Backend 与 Agent Runtime 数据库分别初始化，不能用同一个 DB/schema。

### 9.2 首次需要保留数据后

- 冻结 baseline，之后只能追加 Alembic revision，禁止继续原地修改 baseline。
- 使用 expand/contract；旧/新 API、worker 和移动客户端要能在兼容窗口共存。
- 每次 schema 变化先备份并做 restore drill；不可逆数据变化优先 roll-forward。
- Agent Runtime migration 还要考虑正在运行、等待确认和待恢复的 Run；run/prompt/context/schema
  version 必须可识别，不能让新镜像读不懂活跃 ledger。

## 10. 联合验收与 SLO

后端 join barrier 至少包括：

1. Product Backend 与 Agent Runtime 两个 public `/v1/health/live`、`/v1/health/ready` 通过。
2. 两个 TLS endpoint 通过 CA/hostname/有效期检查。
3. Product Backend/Agent Runtime container 都使用 manifest 记录的 image digest。
4. 两个 Alembic revision 与各自镜像唯一 head 一致。
5. Product Backend JWKS 只暴露公钥；Agent Runtime 能校验 Runtime audience token。
6. Agent Runtime service key 只能访问 Product Backend internal Agent Runtime API，operator key 不可替代。
7. 创建 thread/run、SSE 首帧与终态、reconnect/replay、cancel 通过。
8. 至少一个 Agent Runtime -> Product Backend read Tool 通过；隔离账号上一个可回滚 Action 的
   preview/confirm/apply/idempotent replay 通过。
9. Product Backend OpenAPI、Agent Runtime OpenAPI 与 manifest hash 相等。
10. 两边最近日志无 traceback/critical/fatal/uncaught，且不含 token、健康数据、
    Tool args、capability URL 或文件 body。

初始 test 观测目标：

- Product Backend/Agent Runtime readiness 100% 通过才允许 App 发布。
- 99% 短 Run 在 2 秒内产生首个应用事件（根据真实压测再校准）。
- 100% 高风险 Action 有 confirmation 与 audit。
- 无跨用户数据暴露，失败 Run/Action 必须可见且可关联 request/run/action ID。

## 11. 回滚

### Product Backend

- 新 API 未通过验证时保持旧 current/env；恢复旧镜像。
- migration 后只有旧代码兼容新 schema 才允许 code rollback；否则 roll-forward。
- Product Backend 故障时可在 service gateway 暂停 Agent Runtime internal traffic，不操作 Agent Runtime DB。

### Agent Runtime

- 停止领取新 Run，保存 worker heartbeat/lease 状态，恢复旧 API/worker 镜像。
- Redis 丢失不影响 durable ledger；过期锁由 DB 扫描恢复。
- 不自动 downgrade Agent Runtime DB，不删除 waiting-for-confirmation 或 active Run。
- 如果旧镜像不能读新 ledger/schema，保留新 schema并 roll-forward。

### App

- 已安装的移动客户端无法远程回滚；保持后端向后兼容，通过 feature flag/kill switch
  降级，或增加 build number 发布 hotfix。
- GitHub Release asset 不覆盖；错误包可撤下下载入口，但后续修复必须使用新 tag。

任何失败都保留失败 release dir、镜像、日志、manifest 和备份，用于复盘与回归测试。

## 12. Release manifest

建议最小结构：

```json
{
  "release_id": "20260819T120000Z",
  "environment": "test",
  "product": {
    "commit": "<40-char-sha>",
    "image_digest": "sha256:<digest>",
    "migration_head": "<revision>",
    "openapi_sha256": "<sha256>",
    "public_url": "https://backend-test.lute-momcozylab.luteos.cloud:8443"
  },
  "agent": {
    "commit": "<40-char-sha>",
    "image_digest": "sha256:<digest>",
    "migration_head": "<revision>",
    "product_openapi_sha256": "<sha256>",
    "openapi_sha256": "<sha256>",
    "public_url": "https://agent-test.lute-momcozylab.luteos.cloud:8443"
  },
  "app": {
    "commit": "<40-char-sha>",
    "version": "1.0.0+<build>",
    "flavor": "unified",
    "config_environment": "test",
    "application_id": "com.momcozymai.app.flutterpoc.unified",
    "signing_cert_sha256": "<fingerprint>",
    "apk_sha256": "<sha256>",
    "apk_size": 0,
    "github_tag": "unified-android-v1.0.0-<build>",
    "pages_path": "/unified/"
  },
  "verification": {
    "product_ready": true,
    "agent_ready": true,
    "cross_service_smoke": true,
    "app_online_verified": true
  }
}
```

manifest 不记录 secret、token、真实用户 ID、健康数据或 signed/capability URL。

## 13. `test` 命名切换与下一次发布前的阻断

1. [x] 2026-09-04 已明确选择丢弃 legacy 新栈测试数据：停止并删除
   `momcozy-lab-*-staging` 容器，删除其 PostgreSQL/Redis/MinIO 三个数据卷和
   `momcozy-lab-staging` 网络；未制作数据备份。旧发布指针和私有配置仅作为诊断材料
   归档到 `/opt/momcozy-lab/retired/staging-20260904T070408Z`。随后从空数据卷 bootstrap
   `momcozy-lab-backend-test`，因此本次是已确认的数据重置，不是迁移。
2. [x] 两个 SNI site 已切换到 `/etc/nginx/tls/momcozy-lab-test`；续签服务已改为
   `/usr/local/sbin/renew-momcozy-lab-test-leaf`。相同证书字节继续复用现有内部 CA，
   `nginx -t`、reload 和三个域名健康检查均通过，本次未轮换 CA。
3. [x] 三仓已配置 GitHub `test` environment、`TEST_APPROVAL_ISSUE`、
   `TEST_APPROVERS` 与所需 `TEST_*` secrets；Backend/Agent 发布均通过精确绑定
   repository、run、attempt 和 SHA 的白名单确认门禁。
4. production 部署配置仍已移除。共享基础设施 secret 必须写入 mode `0600` 的私有
   env，并由两个 settings validator fail closed；不能把 `docker compose config --quiet`
   成功当作 secret 有效。
5. 旧线上 image/release 曾出现 `APP_VERSION`/OpenAPI 与真实 commit 漂移。新栈继续由
   release manifest 注入精确 commit，并校验 public OpenAPI、镜像 digest 和 worker
   heartbeat generation，禁止沿用手工维护的版本字符串。
6. 切换前重新检查 `8443 protocol options redefined`、安全组仅开放 8443、两个 SNI
   host 和旧入口 readiness；不得依赖公网 80 或 HTTP redirect。
7. test restore drill、真实 Agent 队列 drain/恢复、正式签名 APK 和 Android
   真机/BLE/MotionPose smoke 必须在下一次发布门禁中真实通过，不能由本地单元测试替代。

## 14. 实施里程碑

### M0：消除阻断

- [x] App 使用服务器现有长期 CA，并限定两个 test SNI 主机；CA subject 的 legacy
  名称按兼容策略保留。
- [x] 固定 local Product Backend 端口为 8769。
- [x] 增加 unified flavor/applicationId、namespaced GitHub tag/asset 和 Pages `/unified/`；
  首个 test 发布使用 build 57，后续 build number 只递增不复用。
- [x] 两个后端 CI 产出 full commit/digest manifest，App 冻结两个 OpenAPI artifact；首次发布时再冻结三仓 release commit。
- [x] legacy 新栈数据按 resettable bootstrap 处理，并于 2026-09-04 完成重置。

### M1：双后端可独立部署

- [x] 建立 `/opt/momcozy-lab` immutable release 目录约束和两服务 release manifest schema；当前以 App test workflow 承担最小联合编排，不新增 `ops/`。
- [x] Product Backend/Agent Runtime 两个版本化 Nginx site 已安装为
  `momcozy-lab-product-backend`/`momcozy-lab-agent-runtime`，复用
  `momcozy-lab-test` network 和 `product-backend` alias；
  不修改旧 `momcozy-api` site/upstream。
- [x] 为 Product Backend/Agent Runtime 分别实现 preflight、backup/migrate、deploy、verify、rollback。
- [x] Agent Runtime test 发布在显式 migration 前停止 API/worker；真实 active Run drain/recovery 仍需首次部署演练。

### M2：联合构建发布

- [x] 实现三仓独立 CI gates。
- [x] 扩展 App verifier：双 URL、release signing、两个 OpenAPI hash 与 live JSON 合同。
- [x] 碰撞门禁已验证端口、owner、旧栈容器身份、unified tag/Pages namespace。
- [x] 实现 Product Backend -> Agent Runtime -> App cross-service join barrier。
- [x] 只在 join barrier 后发布同一个预构建 APK，并写包含两服务身份的 App manifest。

### M3：生产化

- [x] 镜像推送私有 GHCR 并以 digest 部署；SBOM/漏洞扫描尚未实现。
- [x] 实现并配置不依赖套餐的 test 白名单二次确认与正式 release signing；受信任公网
  TLS 和 Android AAB 尚未实现。
- 完成备份 restore drill、压测、告警、SLO dashboard、真机/BLE/MotionPose smoke。
- 引入兼容 Alembic 历史、JWT 双 key 轮换窗口和定期故障演练。

## 15. 完成定义

只有满足以下条件才算方案落地：

- 三个仓库可从 clean commit 独立构建，所有制品能追溯到 commit/digest。
- Product Backend/Agent Runtime 独立迁移、独立备份、独立 readiness、独立回滚。
- Agent Runtime 只通过私有 typed API 使用 Product Backend，App 使用两个显式 HTTPS origin。
- 旧 `/opt/momcozy` 栈的 8000 upstream、8443 共享 listener、Compose project、Nginx
  site 和 Pages 根发布在整个新栈发布前后保持相同 owner 与 readiness；新 test 服务
  使用 8001/8002 和独立 SNI/Pages namespace。
- 联合 release manifest 能证明契约 hash、迁移 head、镜像、App 版本和 APK digest。
- App 发布前两个后端及跨服务 smoke 均已真实执行并通过，而非 skipped。
- 发布后 GitHub Release、Pages、APK checksum、签名、双 embedded URL 与 manifest 一致。
- 回滚和 restore drill 在 test 实际演练通过。
