# 专业记录与护理方案

`app/modules/documentation` 是双端共用的 Product Backend 业务域。使用既有账户、服务、预约与授权，不保存另一个客户端病例副本。

## 访问与版本

- 私密 `GET /v1/care/appointments/{id}/documentation`、`/notes/{note_id}` 仅允许当前分配的、处于 active 状态的 IBCLC，且需要该服务的 `ibclc_case` 授权。读取会审计；授权检查在幂等重放之前执行。
- `PUT /note` 接受四段 SOAP 与 `expected_revision`、`expected_version`。首次创建为 `0/0`。保存、签署、修订、保存方案及发布均要求 `Idempotency-Key`。
- `POST /note/sign` 要求四段完整；签署记录不可编辑。`POST /note/amend` 要求修订理由，复制为新的草稿并保留原签署记录。
- `PUT /plan` 可保存未完成的草稿。`POST /plan/publish` 校验当前专业记录已签署、标题/总结/目标/任务完整，然后创建不可变发布快照。
- 发布事务串行锁定 owner → episode → provider → appointment → consultation。重复发布请求复用原快照；首次发布启动服务周期，修订不会重复扣咨询次数或重置周期。

## 妈妈端

`GET /v1/care/appointments/{id}/summary` 只返回本人预约、服务与已发布方案，没有私密 Note、草稿或签署记录 ID。未发布时 `publication` 为 `null`。撤回专家访问授权后，用户仍可查看自己的已发布方案。

`PUT /v1/care/plan-publications/{publication_id}/tasks/{source_key}` 接受任务状态与 `expected_version`，只允许本人修改当前发布版本。新发布版本保留同一 source key 且内容完全未变的任务状态；修改内容的任务回到 pending。旧发布版本不可再反馈。

## 验证

`tests/test_care_documentation.py` 在独立 PostgreSQL schema 中验证归属/授权、签署不可改写、修订号冲突、并发幂等发布、服务周期和任务反馈。设置 `MOMCOZY_TEST_DATABASE_URL` 后运行；未配置时会显式跳过真实数据库测试。

```sh
python -m pytest -q tests/test_care_documentation.py tests/test_migration_smoke.py
```

迁移为 `20260908_0008`。相关 Flutter 模块是 `modules/ibclc/documentation` 和 `modules/services`，共同使用 `domain/care` 与 `services/documentation`。
