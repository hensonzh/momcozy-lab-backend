# Product API 版本化退役：证据与发布门槛

更新：2026-09-27。**状态：阻断；未确认旧使用者退出，未退役、未部署。**
适用于当前 `backend/` 候选代码替换任何已部署 Product `/v1` 的场景，也适用于已发行 App 仍连接的旧入口。不要把“当前源码已无调用”当成旧 App 或外部消费者已退出。

## 现网只读审计结果（2026-09-27 UTC）

| 入口 | 可核实的事实 | 结论 |
| --- | --- | --- |
| `backend-test.lute-momcozylab.luteos.cloud:8443`（新 staging） | 受信任 CA 下载的 live OpenAPI 与 `docs/openapi.generated.json` 有 **80 项**破坏差异：75 个旧操作缺失（Care 38、IBCLC 15、Agent internal 10、pregnancy diary 5 等），`verify-email` 与 `reset-password` 新增 `confirm_password` 必填，`GET /v1/schedule` 缺 3 个响应字段。可见 Nginx 访问日志覆盖 2026-09-07 至 09-27，但 **09-17～09-19、09-22～09-24 无可见日志**；可见日志中上述 75 个已删除操作的调用为零；但 2026-09-20～09-26 的非探针 `/v1` 请求仍有 **491 次**（其中 411 次 2xx），全部缺可信 build 归属。DB 有 **3 个未到期 active refresh token**，最晚到期日 2026-10-21；`device_sessions.last_seen_at` 未填，不能用它证明退出。 | 零调用的覆盖期不足，且未按构建号/消费者区分；**不能退役**。对旧客户端，新增必填和响应字段变化即使没有路由删除也属于破坏。 |
| `lute-momcozylab.luteos.cloud:8443`（旧统一 API/旧 APK 来源） | GitHub Release `unified-android-v1.0.0-59` 于 2026-09-21 发布，provenance 标记 App `b42985d3…`、旧 Backend `44484424…`、Agent `18682adc…`；APK 有下载记录，但下载量不等于实际活跃人数。该入口 2026-09-21 仍出现 `/v1/plans` **4 次**、`/v1/care-overview` **1 次**和 Agent 请求；2026-09-20～09-26 的非探针 `/v1` 请求 **35 次**（其中 33 次 2xx，`/v1/plans` 8 次），缺 build 归属。旧 DB 有 **24 个未到期 active refresh token**（2 个用户），最晚到期日 2026-10-21。旧 App 源码仍引用 Care/IBCLC/Google 登录等旧操作。旧 live OpenAPI 与当前候选另有 **124 项**破坏差异。 | **存在未迁移旧客户端或至少未失效的会话；不能假定退出。旧统一 API 必须保留；旧包分发是否关闭需要独立审批，且关闭下载不等于旧安装已退出。** |

以上只报告汇总数，不复制 IP、User-Agent 原文、用户标识、令牌或请求体。Nginx 当前 `/etc/logrotate.d/nginx` 为 `daily` + `rotate 14` + `notifempty`，不能凭现有文件形成连续 30 天的零访问证明；没有日志的日子 **不是** 零请求。当前 `X-Momcozy-Client` 只标识 `flutter` 一类而不含可信 build，旧包无法补发版本号；按访问日志也无法可靠区分安装者与内部烟测。数据库统计必须在正确的环境/数据库中以 `BEGIN READ ONLY` 执行，不能混用两套入口的证据。

## 分阶段方案：先兼容并存，再迁移，最后退役

1. **冻结和列清单。**保存每个 live host 的 TLS 校验 OpenAPI SHA-256、Backend/Agent 不可变 manifest 和全部已发布 App build/provenance；建立操作→客户端（旧统一 APK、Care/IBCLC 工作台、Google 登录、内部 Agent、支付 webhook/后台任务等）映射。单独审查服务器间私有调用与回调，不依赖 Flutter `rg`。保留旧下载站、旧 DB、JWT 验证和回滚快照。
2. **推出真正的版本边界，而非原地改写 `/v1`。**当前候选本身仍注册在 `/v1`，**尚无 `/v2`**。在老用户还未退出时，只能：
   - 为旧 `/v1` 保持完整现行行为，同时新增 `/v2` 并让新 App/Agent 指向它；同一主机发布前，当前 live→candidate OpenAPI 兼容门禁必须通过；或
   - 按 `app/docs/deployment/dual-staging-release-lanes.md` 的隔离方案先建立**独立主机/域名、数据库和用户身份边界**，让旧 App 继续打旧 Backend，新的发行包只打新服务。新主机尚未准备，不能把旧域名重指向不兼容的候选。
   `confirm_password` 和 schedule 的破坏性变更只能放入新版本：旧 `/v1` 保持可接受旧 payload 与旧响应字段；仅新 `/v2` 强制双重确认（新 App 前端仍可自行双输）。不可简单复制旧 router 到新 DB：旧 Care/IBCLC/Agent 涉及数据、权限、支付/RTC/webhook 和服务密钥。
3. **观察并迁移。**发布带可审计 build/通道标识的新客户端及单独 `/v2` 调用统计（客户端请求头可伪造，须结合发行清单、会话和服务端记录交叉验证），老客户端的零调用期不能从这一天之前倒推。为旧端访问日志准备至少 30 个完整 UTC 日的**脱敏每日聚合与连续性证据**（或在保留策略和隐私评审后安全存储原始日志），包括 2xx、4xx、5xx 和仍连接的 internal/worker；对全天零请求的日期，需要独立的日志管道心跳/归档证明，不能凭 `notifempty` 推断；监测活跃 refresh token、旧 App 下载入口和关联服务。旧版能持续刷新 30 天令牌，因此零调用 30 天不意味着客户端已退出；要核实旧会话全部到期或主动退出，并核对 Google/IBCLC 等无法由 App 登录统计覆盖的消费者。若无法区分新旧会话，应保守计入所有未到期 token。
4. **评审退役窗口。**至少满足：已发布 build/独立客户端完整盘点及迁移/退役签收；旧包不再可被新用户安装（经发布负责人批准后），所有旧入口无有效使用会话；从最终切换后开始连续 **30 个完整 UTC 日**已被监测且待删路由调用量为 0；覆盖所有可访问主机和内部入口，受影响的独立 `/v2` 或新 host 的用户流量已真实验证；Backend/Agent/App 门禁全绿，数据库/资源迁移与回滚演练通过，用户沟通/数据留存和隐私审批完成。任何访问、遗漏日期或新增安装，都应重新评估或重启观察期。
5. **仅在前述全部核实后提出退役发布。**运行下面的离线检查，将结果和**日志聚合来源、查询时间、完整客户端清单及人工签署**留在受保护的变更单。工具零退出码只表示“可进入人工评审”，**不能跳过** `.github/workflows/backend-delivery.yml` 的 live→candidate 兼容门禁；若要允许受控 retire，需要另行设计带审批、锁、候选契约和快照验证的正式 workflow，不能靠改脚本返回值绕行。先停止旧入口发包，再计划代码/路由移除；出现旧请求时保留/回滚兼容入口，绝不能只回滚 DB schema。

## 离线门禁（尚不授权发布）

`backend/scripts/summarize_retirement_access.py` 只读解析标准 Nginx combined 与旧 `momcozy_timing` JSON 访问日志（含 `.gz`），按**契约路由模板**输出破坏操作次数及无法按 build 归属的其它 `/v1` 流量，不输出 IP、原始 URL、User-Agent、请求 ID 或 token。工具包含 4xx/5xx 以免把登录失败误算为用户退出；`days_with_requests` 不是完整日志覆盖证明，无请求的日期须另有日志管道心跳/归档证据。当前两种边缘日志均未记录可信 build，任何非探针 `/v1` 请求都计入 `unattributed_v1_requests` 并阻断。注意 `/v1/model-assets/` 的 Nginx access log 被关闭，若涉及其消费者，还须有其它可审计来源。

`backend/scripts/check_api_retirement_readiness.py` 使用 live OpenAPI 字节流的 SHA-256 锁定证据和主机，要求嵌入上述 `access_summary`，并针对每条破坏操作、其它未知 `/v1` 流量、坏日志行、连续观察日期、未到期会话数、服务消费者和已发行客户端清单逐项阻断。对仍然留在 `/v1` 且变更了契约的操作，要求对应 `/v2`；完全删除的功能无须凭空新增同名 v2 功能，但必须经过完整迁移/退出签收。**不要提交私有原始日志；证据 JSON 的字段需要由独立来源核对，纯手工填写 `0` 不能构成事实。**

```bash
# 以下命令只读取文件；分别对每个待退役 host 的 TLS-verified OpenAPI 和对应证据执行。
# 仅在获准处理日志的主机上运行汇总，不复制或提交原始日志；若在本机执行，
# 应先通过隐私评审。--start/--end 是完整 UTC 日期，不包含当前半天。
cd backend
.venv/bin/python scripts/summarize_retirement_access.py \
  --current /path/to/verified-live-openapi.json \
  --candidate docs/openapi.generated.json \
  --host backend-test.lute-momcozylab.luteos.cloud \
  --start 2026-08-27 --end 2026-09-26 \
  /path/to/reviewed/site.access.log* > /path/to/redacted-access-summary.json
# 由独立负责人核对输出、DB 聚合、客户端来源、缺失日期和日志源；
# 将汇总置入证据 JSON 的 access_summary 字段，再进行仅供人工评审的检查。
.venv/bin/python scripts/check_api_retirement_readiness.py \
  --current /path/to/verified-live-openapi.json \
  --candidate docs/openapi.generated.json \
  --evidence /path/to/reviewed-redacted-evidence.json \
  --host backend-test.lute-momcozylab.luteos.cloud
```

证据字段：`contract_sha256`（live 原字节 SHA-256）、`scope_host`、`window_start`、`window_end`（昨天 UTC，不含当天）、`observed_days`（窗口内每个完整 UTC 日）、`access_summary`（汇总脚本输出，含每条破坏操作的 `operation_calls`、`unattributed_v1_requests`、坏日志行数和流 SHA-256）、`unexpired_refresh_tokens`、`unmigrated_service_consumers`、`client_inventory_complete`、`released_clients`（每项 `build` 和 `state=migrated|retired`）、`legacy_distribution_disabled`。此脚本只检验**同一 host 的 `/v1` → `/v2` 路径版本化**；若选择完全隔离的新 host 作为版本边界，需单独评审 host→客户端绑定、旧 host 原样保留和独立身份/数据库，不应拿这个工具去为跨 host 部署“开绿灯”。日志汇总的 `log_stream_sha256` 是按输入顺序解压/解码后的流指纹，不取代原始文件与日志管道的留存证明；`observed_days` 不可简单复制 `days_with_requests`。该机器检查仅检查**格式、一致性和阻断信号**，不验证调查真实性、客户端行为、保密要求或工作流审批；不应该用伪造的 JSON 解锁。

**目前阻断**：旧入口 24 个未到期 token 和旧包可下载；新 staging 3 个未到期 token；14 天滚动日志不足；客户端 build 不可辨；当前候选没有 `/v2`；80/124 处兼容差异；独立北美环境、迁移、发布测试仍未就绪。无需、也不得为通过此门禁而注销用户或关闭旧功能。
