# Resend 邮箱验证接入 — 后续执行清单

> 快照日期：2026-09-27。状态：**代码有 SMTP 适配与模拟测试，真实 Resend / staging 端到端尚未验收**。
> 本清单是交接资料，**不是**注册第三方账号、购买套餐、创建密钥、修改 DNS、部署或发送邮件的授权。下一位执行者须先复核最新状态及用户确认；完成一项后记录日期、环境与不含秘密的证据。

## 0. 先明确范围与目标

- [x] 继续由 Product Backend 管理邮箱、密码、8 位验证码、账号状态与会话；**Resend 只发送事务邮件**，不是把用户账号迁移到 Resend。
- [ ] 验收目标：真实外部邮箱收到注册验证码 → 在 App 输入验证码 → Product Backend 核验、激活账号并签发会话 → 退出后重新登录；另测忘记密码邮件 → 验码 → 重置 → 新密码登录。**“SMTP 接收成功”不是“用户收到邮件”。**
- [x] 用户已确认后续会重新构建 iOS App；**Build 59 只保留历史记录，不作为本次 Resend 接入的兼容或发布目标**。Resend 接通仍不能单独宣称 TestFlight 外部分发完成；历史状态见 `app/docs/ios/testflight-staging-build-59-handoff.md`（工作区根目录）。

## 1. 当前已知事实（接手后先复核）

| 范围 | 当前状态 / 入口 |
| --- | --- |
| Flutter App | `app/lib/core/auth/momcozy_auth_api.dart`、`app/lib/features/auth/presentation/auth_page.dart` 有注册、验码、登录、重置流程；App **不**直接调用 Resend，也不能内置 API key。 |
| Product Backend | `app/modules/auth/router.py`、`account_lifecycle.py` 管理挑战和会话；`app/modules/auth/email.py` 加密入队、通过 SMTP STARTTLS 发出；`scripts/deliver_auth_emails.py` 为 worker。以上 Backend 路径相对 `backend/`。 |
| 部署拓扑 | `backend/docker-compose.deploy.yml` 有 `api` 与 `auth-email-worker`；两者从目标环境同一私有服务 env 读取配置。`env/staging.env.example` / `env/production.env.example` 只有占位值。 |
| Resend 适配 | 现有 SMTP 实现适合 `smtp.resend.com:587`、用户名 `resend`、密码为 Sending access API key；**不支持直接把隐式 TLS 端口 465 填入当前 STARTTLS 实现**。`backend/tests/test_auth_email_smtp.py` 是伪 SMTP 测试，不发真实邮件。 |
| 本地测试 | 2026-09-27：Backend 邮箱相关测试 **86 通过、2 跳过**；App 定向认证测试 **42 通过**。均未证明真实 Resend 投递、实际 iOS 安装包或当前 staging 云端闭环。 |
| 工作区 | 2026-09-27 检查时 Backend 与 App 都有**其他未提交变更**，含认证契约改动；本清单配套文档和 SMTP 测试是在交接过程中准备的，接手时应确认它们在所用分支中可见。不要覆盖/清理，也不要将未提交改动当作可发布源码。 |
| 实际服务 | **没有核实**当前 staging 云端 SMTP 配置、Resend 项目/域名/key、worker 是否健康或收件箱投递；本机 `backend/env/local.env` 的 SMTP 配置未启用。不要据此推断云端配置。 |

Resend 官方在 2026-09-27 展示事务邮件 Free：**$0/月、3,000 封/月且 100 封/日、最多 3 个域名**，无需先订阅付费套餐或申请生产发信审批；超额策略和是否要求付款资料，以注册时官方界面再次确认。免费额度可能变化，不能当作未来保证。

## 2. 接手时只读复核（没有凭据也可做）

- [ ] 先看工作区 `AGENTS.md`、`backend/docs/resend-auth-email-integration.md`、`backend/docs/account-authentication.md`、`backend/docs/deployment-runbook.md` 和 `app/docs/ios/testflight-staging-build-59-handoff.md`。确认这些文件是否已纳入当前分支；若缺失，以本清单和源码重新核对。
- [ ] `git -C backend status --short --branch`、`git -C app status --short --branch`、`git log`；确认**将要部署的 Backend commit 与将要重新构建的 App commit**，而不是以 Build 59 验收。特别核对 `/verify-email`、`/reset-password` 的 `confirm_password` 契约：2026-09-27 本地未提交后端改动要求两者都传确认字段，本地 App 工作树已相应调整；两边改动均须审查、测试、提交后，再分别部署与重新构建。历史 Build 59 不作为兼容门槛。
- [ ] 只读取当前云端 `APP_ENV`、邮件 worker 存活、SMTP 配置**是否存在**、发件域名是否匹配；只报告布尔值/非秘密配置，不打印 env 全文、密钥、用户邮箱、验证码或邮件正文。不要把本机 `env/local.env` 当云端真相。
- [ ] 复核 Resend 最新免费额度、SMTP 参数、数据处理条款以及现有 DNS 管理方式；本清单日期之后的价格/规则可能变动。

## 3. 用户/域名管理员决策（每一项独立确认后才能操作）

- [ ] **域名**：用户指定公司拥有且允许用于认证邮件的主域名和发送子域名（例如 `auth.<公司域名>`），指定 DNS 管理人；不要自己假设 `momcozy.com`、`luteos.cloud` 或个人 `163.com` 邮箱具备发件域名管理权。API 主机域名与发件域名可以不同。
- [ ] **供应商账户与区域**：公司 Resend 账号的负责人、staging 使用的发送区域及免费/付费选择；若需新注册账户、付款方式、订阅或权限变更，行动前再次确认。
- [ ] **域名验证**：经授权在 Resend 添加指定子域名；按控制台**当时生成**的 DKIM、SPF 等记录核对 DNS 冲突并添加；建议审核 DMARC。以 Resend 控制台显示 verified 为门槛，记录值不能猜、不能用未授权的域名。修改 DNS 前确认。
- [ ] **第三方数据处理**：邮件会把收件人地址和一次性验证码传给 Resend；确认公司接受其数据处理、区域和日志/保存策略后再发真实验证码。不要在聊天里传任何密码、验证码、密钥。
- [ ] **API key**：单独确认创建仅有 Sending access、限定到已验证域名的 staging key；production 另建不同 key、另行授权。通过公司受控密钥通道交付；**不**贴聊天、不进 Git、不放进 IPA/Flutter `dart-define`、截图或 CI 日志。Resend key 可能只显示一次。
- [ ] **云端变更**：明确批准目标 staging 环境、维护窗口、现有 SMTP 回退值的安全留存、更新私有 env、重建 API/mail worker 和真实邮箱测试。只读排查不等于部署授权。production 必须单独批准。

## 4. 实施（仅在上述门槛完成后）

- [ ] 保留现有 `AUTH_EMAIL_TOKEN_KEY`；它是验证码 HMAC/队列密文密钥，**不是 Resend API key**，切换它会影响未过期验证码和队列消息。API 与 worker 必须使用同一值。
- [ ] 将以下变量写入**目标环境的私有 Backend env**，而不是 `.example`、App 配置或仓库；`<...>` 均为占位符。实际发件地址必须属于 Resend 已验证的子域名：

  ```dotenv
  AUTH_EMAIL_FROM=no-reply@<verified-sending-subdomain>
  AUTH_SMTP_HOST=smtp.resend.com
  AUTH_SMTP_PORT=587
  AUTH_SMTP_USERNAME=resend
  AUTH_SMTP_PASSWORD=<staging-sending-access-api-key>
  ```

- [ ] 在目标服务器/容器确认出站 TCP 587 与证书校验 STARTTLS 可用；不要在诊断输出中暴露凭据。无需配置“Resend URL”到 Flutter；SMTP host 即后端连接地址。
- [ ] 按 `backend/docs/deployment-runbook.md` 的受保护发布入口操作；先解决 Backend 脏树，确定并通过 CI 验证将要部署的 commit，再安全重建相关服务。单改宿主机 env **不会**自动刷新运行中容器的环境。不要绕过发布脚本直接更新线上服务。
- [ ] 后端与当前 App 认证契约对齐并验收后，按 App 发布流程**另行预留新 build number 并重新构建**；记录 Backend commit、App commit、API 环境及新 build number。若需向 TestFlight 上传、外测提审或启用公开链接，仍按 iOS 发布门禁另行确认；不得覆盖 Build 59 二进制。

## 5. 验收与回退记录模板

- [ ] 静态/模拟测试：`cd backend && .venv/bin/python -m pytest -q tests/test_auth_email_smtp.py tests/test_account_security.py tests/test_account_lifecycle.py tests/test_account_review_regressions.py tests/test_auth_api.py tests/test_settings.py`；`cd app && flutter test --no-pub test/core/auth/momcozy_auth_api_test.dart test/core/auth/account_session_lifecycle_test.dart test/features/auth/consumer_auth_page_test.dart`。测试可能随分支变化，运行前复核文件与工具链。
- [ ] 云端健康：`api`/`auth-email-worker` 正常，队列 pending/retry/failed 无异常堆积；保留操作时间和**无秘密**的健康证据；业务就绪健康检查不能代替真实发信测试。
- [ ] 经用户同意的测试邮箱：注册收 8 位码（检查收件箱/垃圾箱）→ 先检查验证码 → 设置密码并完成账号激活 → 退出并重登 → 忘记密码收码 → 重置并以新密码登录；验证过期码、错误码及重发行为；不使用真实妈妈/宝宝数据。
- [ ] 对照 Resend 仪表盘投递状态、邮件实际收件、域名认证 SPF/DKIM/DMARC；确保日志、反馈和仓库均不包含 key/验证码/邮件正文。记录测试环境、Backend commit、**新 App build**、发送域名、时间及成功/失败，不记测试密码或完整验证码。
- [ ] 若失败：先分辨 DNS/端口/TLS/认证/配额/worker 队列/邮件投递与 API 契约问题；按批准的回退方案恢复旧 SMTP 私有配置并安全重建服务，确认队列与现有登录可用。**不要**为回退轮换 `AUTH_EMAIL_TOKEN_KEY`。
- [ ] 只有云端与真实收件箱闭环通过，才把“Resend staging 接入完成”标为完成；生产迁移和外部 TestFlight 体验分别验收。

**官方资料（执行前核对最新版本）：** [Resend 价格](https://resend.com/pricing) · [SMTP 参数](https://resend.com/docs/send-with-smtp) · [域名验证](https://resend.com/docs/add-a-domain) · [API key 权限](https://resend.com/docs/create-an-api-key) · [生产发送审批说明](https://resend.com/docs/knowledge-base/does-resend-require-production-approval)。
