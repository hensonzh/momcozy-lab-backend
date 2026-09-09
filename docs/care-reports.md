# 服务报告与专业跟进

已实现对话关联/来源、持久化生成任务、结构化生成、专家跟进列表、按服务日期读取与复核。用户端服务对话入口、用户可见反馈和完整服务阶段闭环仍在实施。原稿 `DailyFollowupPanel` 中的固定对话、过去日期自动“已反馈”和演示用户画像未迁移为产品事实。

## 运行方式与边界

现有 Agent Runtime 已有 PostgreSQL 对话/运行记录、模型提供方封装及工具权限。本次保留这些基础设施。每日报告采用固定的读取、结构化生成、校验和复核流程，生成节点不开放工具、不修改方案、不直接联系用户；不引入新的通用 Agent loop 或多智能体框架。

- Product Backend 拥有服务、授权、报告生成任务、不可变报告版本、专家复核与业务事件。
- Agent Runtime 拥有已完成的对话与模型调用能力。普通聊天不自动属于某个服务；服务对话必须显式绑定服务周期，并通过 Product Backend 校验所有者与当前授权。
- 报告来源只包含该服务的授权资料、记录、已发布方案，以及明确关联该周期的已完成对话。私密 SOAP 不作为模型输入，也不复制到用户接口。
- Runtime 的报告接口使用单独的服务凭据，只接受受控的 Product 请求；专家浏览器不持有这份凭据，也不使用妈妈身份令牌。
- Flutter 只消费 Product 的领域契约。报告显示来源、数据截至时间、生成状态和复核结果，不读取 Runtime 数据库或解析自然语言状态。

## 生命周期与恢复

刷新先读取有界来源并生成来源清单/内容哈希，随后在周期锁下重新核对分配和授权版本。同一来源快照复用已有报告，变化产生新版本。没有资料时显示等待记录，不生成虚构的用户状态。

报告任务按 `queued → running → ready / failed / cancelled` 持久化。worker 用租约领取、超时与有界重试；模型调用在数据库事务外执行。保存结果前再次核对租约、周期分配、病例授权和 AI 授权版本。过期任务和撤回前的在途结果不能替换当前报告。

输入来源、提示词/结构版本、模型标识、生成尝试与安全错误码保存在任务/报告记录中；业务内容不写入常规日志。来源接口按所有者、周期、时段和完成状态过滤，排除删除的对话。超出输入预算时必须明确显示覆盖范围，不能假装包含全部来源。

## 数据、动作与复核

报告版本关联 `episode_id + report_date + timezone`，包含来源引用、问答、AI 摘要/待核对点、数据不足项、模型与提示词版本及截至时间。每个摘要要点须引用存在的来源，输入中的指令只作为被引用的用户内容处理。

只有当前分配、账号有效且病例/AI 授权有效的专家可读报告或提交复核。确认与反馈修改都针对指定报告版本，反馈至少五个字符；旧标签页不能复核后来生成的新内容。复核留存版本历史，并产生共享服务事件。生成成功产生工作提醒；私人复核事件不作为用户通知。

“确认回答/反馈修改”是专业复核记录，与发给用户的服务反馈分开。用户可见反馈需要明确的发布操作；发布前显示具体内容。报告更新、专业复核和方案发布各有独立状态，不从日期推断“已完成”。

前端采用现有轮询/恢复前台刷新方式；等待、失败、授权撤回、数据不足与新版本均有明确页面状态。今日跟进按客户聚合当前专家负责的多个服务，同时保留每个周期的报告与服务日期。

## 配置与运行

1. Product 应用迁移到 `20260908_0012`。配置 `CARE_REPORT_RUNTIME_URL` 为已知 Runtime 源站；生产环境要求 HTTPS，不含路径、用户信息、查询或片段。
2. 两个服务配置相同的独立 `CARE_REPORT_SERVICE_KEY`（至少 32 字节，与已有服务凭据分开）。Runtime 沿用现有 `AGENT_MODEL_PROVIDER`、模型及其凭据配置。
3. 启动 Runtime API 和 Product API，再启动 Product worker。未配置生成服务时请求返回明确的不可用状态。

```sh
python -m app.workers.care_reports
# 或使用可选本地 Compose profile（先完成迁移并配置两个服务）
docker compose -f docker-compose.local.yml --profile reports up -d care-report-worker
```

Test Compose 同样提供可选 `reports` profile，复用明确的发布镜像。此次仅修改运行配置，未启动云端 worker 或发布服务。

worker 每分钟分页扫描当前服务；没有记录保留 `waiting_for_record`。来源相同不会重复调用模型，失败快照的定时扫描不会无限重试；手工更新可创建重试版本。领取租约为 180 秒，生成总超时 140 秒，每版本最多三次尝试。工作进度保存在 PostgreSQL，进程重启后继续领取到期任务。

Runtime 在 JSON 解析前限制来源请求为 48 KiB、生成请求为 128 KiB；结构化生成输入本身最多 96 KiB，Product 使用 80 KiB 来源预算。报告记录真实省略条数和截断标记。专用 HTTP 客户端限制响应大小、不跟随重定向、验证来源哈希与引用。

## 接口与验证

- Product：`GET /v1/ibclc/followups`；`GET/POST /v1/ibclc/episodes/{id}/reports`；`GET .../reports/history`；`POST /v1/ibclc/reports/{id}/reviews`。
- Runtime：内部 `POST /v1/internal/care-reports/sources` 和 `.../generate`。浏览器只访问 Product；接口声明在各自导出的 OpenAPI 中。
- 来源契约归 Runtime 所有。更新后运行 `python scripts/sync_care_report_contract.py --source ../agent/docs/openapi.generated.json`；`test_care_report_contract.py` 对照完整字段/限制。
- PostgreSQL 测试覆盖去重、不同来源版本、两个 worker、过期租约、有界失败重试、授权撤回、私人 SOAP 排除、多服务完成条件和并发复核冲突。工作提醒测试验证单次生成事件及精确日期。
- Flutter 覆盖来源/问答、反馈校验、刷新恢复、拒绝后清除数据、日期深链、小屏双倍字号；1024/1280px 报告及复核区截图已目视检查。
- [Runtime 评测集](../../agent/docs/care-report-evals.md) 的八项离线契约检查通过。未配置真实模型凭据，未执行真实模型质量评测；测试用合成输出不能证明医学准确性，专业复核仍是报告流程的一部分。
