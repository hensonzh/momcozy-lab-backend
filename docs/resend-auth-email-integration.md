# Resend 事务邮件接入：邮箱注册、验证与密码重置

状态：**本地适配已验证，真实服务尚未接通**（2026-09-26）。本文件不是云端部署授权，也不保存任何真实 API key。

下一位执行者请先看 [`resend-auth-email-handoff-checklist.md`](resend-auth-email-handoff-checklist.md) 的逐项确认和验收门禁。

## 范围与现有实现

继续由 Product Backend 管理邮箱/密码、8 位验证挑战、会话与密码重置；Resend **只负责发送邮件**，不充当身份认证服务。App 仍调用 `/v1/auth/register`、`/v1/auth/verify-email`、`/v1/auth/login` 和密码重置接口。**仅切换 SMTP 本身不要求改 App 代码；但用户已确定后续会重新构建 App，Build 59 不作为本次发布候选。**新构建必须与当时部署的 Backend 认证契约一致，详见交接清单。

后端 `QueuedAuthEmailSender` 先把验证码邮件加密入库；`auth-email-worker` 使用 `SmtpAuthEmailSender` 发送，发送成功后清除队列密文。现有 SMTP 实现支持**证书校验的 STARTTLS**，适合 Resend 的 587 端口；**不能**把 465 隐式 TLS 端口填入此实现。现有重试是 at-least-once，SMTP 已接收后进程异常有可能重复发同一验证码。服务商接收成功不等于收件箱投递成功，后续需分别验证。

## 官方先决条件（需要用户／域名管理员办理）

1. 确认公司拥有并允许用于 Momcozy AI 验证码邮件的发件域名。建议使用专用子域名，例如 `auth.<company-owned-domain>`；这不是后端 API 域名。不要未经确认直接使用 `momcozy.com`、`luteos.cloud` 或个人 `163.com` 地址作为发件域名。
2. 在**公司管理的 Resend 账号**中添加该子域名，选择符合收件用户分布的发送区域。Resend 生成域名专属的 DKIM / SPF 等 DNS 记录；DNS 管理员审核冲突后添加，待 Resend 显示 verified；建议补充 DMARC。不要预先猜测 DNS 记录值。
3. 创建仅有 **Sending access** 且限制到该域名的 Resend API key。Resend 的 key 仅显示一次；通过受控的服务器私有 env / Secret Manager 交付，不发到聊天、不写到仓库、截图或日志。staging 与 production 使用**不同 key**，生产发件域名和策略需单独确认。
4. 确认服务器或容器可以出站连接 `smtp.resend.com:587`，且审核网络/邮件服务条款与用户数据处理要求。邮件服务会收到收件人邮箱和一次性验证码，因此这是向第三方处理认证数据。

官方资料：Resend 的 `Send emails with SMTP`、`Add and verify a domain`、`Create an API key` 文档；具体界面和 DNS 值以创建时 Resend 显示为准。

## 对应的现有配置（**示例值，不是真实凭据**）

在目标环境已有、权限 `0600` 的 **Product Backend 私有 env**（staging 与 production 分开）中更新以下变量。保持现有 `AUTH_EMAIL_TOKEN_KEY` 不变；API 与邮件 worker 必须共享该值，以免已排队邮件无法解密。

```dotenv
AUTH_EMAIL_FROM=no-reply@auth.example.invalid
AUTH_SMTP_HOST=smtp.resend.com
AUTH_SMTP_PORT=587
AUTH_SMTP_USERNAME=resend
AUTH_SMTP_PASSWORD=<RESEND_SENDING_ACCESS_API_KEY_FOR_THIS_ENVIRONMENT>
```

`auth.example.invalid` 只是**不可发送的占位域名**；换成经过公司批准、在 Resend 已验证的发件子域名后才能使用。不要把 Resend key 写进 `AUTH_EMAIL_TOKEN_KEY`（它是既有邮件挑战加密/HMAC 密钥），也不要把 SMTP 密钥放进 Flutter `dart-define`、App IPA、GitHub 公开日志或 TestFlight 审核备注。

服务器 env 是由受保护的 `backend-delivery` 工作流读取的共享私有文件；真实部署必须遵守 `deployment-runbook.md` 和 `app/docs/deployment/environment-workflow.md`，不要直接在服务器手动 `docker compose up/down`。仅改私有 env 并不会自动替换正在运行的容器环境，需要经授权的部署/安全重建 API 与 `auth-email-worker`；先确认当前 staging 源码与用户未提交工作，不要从脏工作树发布。

## 操作门禁与验收

- **已本地验证：** `tests/test_auth_email_smtp.py` 用伪造 SMTP 验证 `smtp.resend.com:587` 的 STARTTLS、用户名 `resend`、API key 作为密码以及发件人/收件人/主题/正文；没有真实网络或邮件发送。
- **待确认：** 公司发件域名及 DNS 管理权限、Resend 账号持有者和发送区域；创建域名/改 DNS、创建 key、把 key 写入 staging 私有 env、部署，均为单独确认点。
- **staging 真实验收：** 域名 verified、出站 587 连通、API 和 worker 读取同一新配置；使用经授权的非真实业务收件箱执行注册收码 → 验证 → 登录 → 忘记密码收码 → 重置 → 新密码登录。核对 Resend 投递状态、SPF/DKIM/DMARC 和垃圾箱，且验证码/SMTP 响应/密钥不进入日志。
- **故障回退：** 保留原已工作的 SMTP 配置副本于安全秘密管理位置。无法发信时，经授权回退私有 env 并安全重建受影响服务；检查 worker 的 pending/retry/failed 队列。不要改变 `AUTH_EMAIL_TOKEN_KEY` 或尝试公开查看队列密文。生产环境需单独批准和验收。

尚未完成上述真实验收前，**不能**宣称外部 TestFlight 用户可以顺利注册登录，也不能用本地伪造邮件测试替代云端投递验证。
