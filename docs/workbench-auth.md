# 独立工作台身份验证

工作台通过 `/v1/ibclc/auth/login` 验证既有邮箱/密码，再通过 `/v1/ibclc/auth/verify` 验证认证器生成的一次性验证码。普通 App 登录和邀请码登录不会授予 `ibclc` 角色。

- 账号必须已有 `care_providers` 记录并处于 active 状态，还需单独配置认证器。
- 挑战有效期 5 分钟，数据库只保存随机挑战的 SHA-256。TOTP 密钥以 Fernet 加密保存在私有凭据表中。
- 每位专家的 OTP 验证用同一行锁串行化；已用过的计数值不可再次使用。5 次错误锁定 10 分钟，失败响应仍提交计数；创建挑战也限频。两个登录端点始终应用 IP 限频。
- 专家会话最长默认 12 小时，`IBCLC_SESSION_HOURS` 可设为 1–24。每次专家 API 请求都核对设备会话、MFA 有效期、用户和专家 active 状态；刷新令牌不会延长 MFA 有效期。
- 不提供公开的专家角色授予或认证器重置接口。管理脚本可配置认证器，轮换会撤销当前工作台会话并使旧挑战失效。

TOTP 使用 [PyOTP 官方实现](https://pyauth.github.io/pyotp/) `2.10.0`，遵循 [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238)。原设计稿的固定演示验证码未迁移到产品代码。

## 配置

先在受控的环境配置中设置 `IBCLC_MFA_ENCRYPTION_KEY`。生成命令如下；密钥需保存在服务配置的秘密存储中，保留密钥才能解密已登记认证器。

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

在已有邮箱账号和专家档案后运行：

```sh
python scripts/enroll_workbench_mfa.py --email provider@example.com
```

命令向终端输出一次带秘密的 `otpauth://` URI，交给对应专家的认证器导入。`--rotate` 替换现有认证器并撤销其工作台会话。脚本不改变专家 active 状态。

不配置该密钥时工作台登录返回 `mfa_not_configured`，普通用户端认证继续工作。新增数据库迁移为 `20260908_0009`，未部署到云端。

## 验证

`tests/test_workbench_auth.py` 使用隔离的 PostgreSQL schema，覆盖两步验证、并发 OTP 重放、HTTP 错误计数持久化、锁定恢复、刷新保留角色、到期/停用和认证器轮换撤销会话。设置 `MOMCOZY_TEST_DATABASE_URL` 后运行。

刷新与退出以设备会话为锁范围，防止多标签页重复轮换同一令牌。发现刷新令牌重用后，HTTP 错误响应会提交令牌组与会话撤销；不会因请求返回 401 而回滚。`tests/test_auth_refresh_postgres.py` 验证并发请求与过期令牌的实际数据库状态。
