# 奶量计划创建流程

本 reference 使用的奶量管理工具都在 `milk_management` deferred namespace 中；如尚未加载，先通过 `tool_search` 加载该 namespace，再调用下文提到的具体工具。

适用于用户需要追奶、稳奶、减奶或调整吸奶/亲喂节奏。

## 原则

- `milk_plan_preview` 只生成草稿，不写 `milk_plan`，也不写 `calendar`；只有返回 `plan_preview_ready` 且 `data.validation.valid=true` 时，草稿才可展示给用户确认保存。
- `milk_plan_mutate(operation="create")` 只能在用户确认后调用。
- create 后同时保存 `milk_plan`，并按用户确认的 `calendar_write_strategy` 把计划日程写入 `calendar`。
- 如果 preview 的 `calendar_delta.has_existing_future_plan_tasks=true`，必须先向用户说明当前未来日程已有多少计划任务、新计划会生成多少任务，并让用户选择“追加到现有日程”还是“替换未来未完成计划任务”；不能替用户默认决定。
- `calendar_write_strategy="append"` 表示保留已有未来任务并追加新计划；`calendar_write_strategy="replace_future_plan_tasks"` 表示删除未来未完成的旧计划任务后写入新计划。用户未选择时，不调用 `milk_plan_mutate`。
- 不要承诺奶量一定增加、稳定或减少。

## 流程

先判断用户属于哪一种入口，再选择评估路径。不要把两种路径混在一起。

### 路径 A：妈妈主动要求特定计划

适用于用户明确说“我要追奶”“我要减奶”“帮我稳奶”，或已经给出目标奶量、增奶量、减奶量。

1. 确认计划方向：`increase_milk`、`decrease_milk` 或 `maintain_milk`。
2. 只做 24h 快速评估：
   - 调用 `milk_assessment_evaluate`，参数建议为 `window_days=1`、`include_today=false`。
   - 目的只是回顾最近完整 24h 每日奶量、吸奶/亲喂/瓶喂记录，并与参考范围比较。
   - 工具结果会包含 `yesterday_feeding_snapshot` 和 `quick_24h_intake`；用它们判断昨日喂养数据是否足够。
   - 不要在这个路径默认调用全面 assessment 或宝宝生长评估；只有用户主动提到体重、身高、增长、摄入是否足够、宝宝发育时，才额外调用 `infant_growth_evaluate`。
3. 如果 `quick_24h_intake.ready=false`，先补问缺失的昨日数据，不要继续制定计划。优先使用工具返回的 `follow_up_questions`；至少需要补齐：
   - 昨日吸奶次数。
   - 昨日吸奶总奶量或每次吸奶奶量。
   - 昨日喂奶总次数。
   - 昨日亲喂次数。
   - 如有瓶喂/补奶，昨日瓶喂或补奶总量。
4. 向妈妈说明 24h 快速评估结果，并确认是否仍要制定该类型计划：
   - 主动追奶：问“是否仍希望制定追奶计划？”
   - 主动减奶：问“是否仍希望制定减奶计划？”
   - 主动稳奶：问“是否生成稳奶计划？”
5. 如果妈妈不确认生成计划，不调用 `milk_plan_preview`；只给生活习惯、记录、观察和必要就医建议。
6. 如果妈妈确认生成计划，调用 `milk_plan_preview`。为了避免重复评估，必须把第 2 步结果放入 `options.prepared_assessment`。
   - 如果用户明确提出目标奶量、增奶量或减奶量，把 `target_daily_ml` 或 `delta_ml` 一并传给 `milk_plan_preview`；工具会返回目标校验结果。
   - 如果用户没有明确目标奶量，由 `milk_plan_preview` 按当前奶量、参考范围和追/减/稳奶规则生成默认目标。
   - 对追奶计划，未知目标时 `milk_plan_preview` 应保证默认目标不低于当前奶量 +50ml，并避免超过 P85 参考上限。
   - 对减奶计划，未知目标时 `milk_plan_preview` 生成默认每日减少量；明确目标时由 `milk_plan_preview` 校验减少量必须在 50ml/天到当前参考日奶量之间。

### 路径 B：妈妈询问哪种计划适合自己

适用于用户说“我适合什么泌乳计划”“帮我生成奶量管理计划”“不知道该追奶还是减奶/稳奶”等。

1. 进行全面评估：
   - 调用 `milk_assessment_evaluate`，参数建议为 `window_days=7`、`include_today=false`。
   - 需要读取基础资料或最新计划时，调用 `milk_snapshot_get`。
   - 用户提到体重、身高、增长、摄入是否足够或宝宝发育时，调用 `infant_growth_evaluate`。
2. 根据全面评估结果推荐计划类型：
   - 奶量偏低：通常推荐 `increase_milk`。
   - 奶量正常：通常推荐 `maintain_milk`。
   - 奶量偏高：通常先观察，或在持续异常时推荐 `decrease_milk`。
3. 问妈妈是否同意推荐的计划类型；同意后才制定。
4. 调用 `milk_plan_preview`，并通过 `options.prepared_assessment` / `options.prepared_growth_assessment` 传入已评估数据，避免重复评估。

### 生成与保存

1. 保存前确认 `milk_plan_preview` 返回 `plan_preview_ready`、`data.requires_confirmation=true` 且 `data.validation.valid=true`；不要自行绕过工具边界。
2. 用简洁语言展示：
   - 计划目标。
   - 执行天数。
   - 每日关键安排。
   - 日程差异：当前未来已有计划任务数、新计划任务数、追加/替换后最终任务数。
   - 观察指标。
   - 安全提醒。
3. 请用户选择：确认保存方式（追加或替换），或修改某一条任务/某个时间点/某一段阶段。没有已有未来计划任务时，可以只问“是否确认保存”。
4. 如果 `milk_plan_preview` 返回 `plan_preview_needs_revision`、`plan_preview_not_recommended` 或 `data.validation.valid=false`，不要展示“确认保存”；只说明需要修改的点，并重新生成/调整草稿。
5. 用户确认保存后，调用 `milk_plan_mutate(operation="create")`；如用户选择追加，传 `calendar_write_strategy="append"`；如用户选择替换，传 `calendar_write_strategy="replace_future_plan_tasks"`。
6. 用户要求修改草稿时，按要求编辑完整草稿，再调用 `milk_plan_preview` 或 `milk_plan_mutate` 内部校验；校验通过并再次确认后，才调用 `milk_plan_mutate(operation="create")`。
7. 修改边界：
   - 追奶：修改后吸奶任务次数必须大于当前吸奶次数；相邻两次吸奶间隔不超过 5 小时。
   - 减奶：目标奶量不能超过当前奶量；吸奶任务次数/频次不能超过当前。
   - 稳奶：吸奶任务次数不能改变。

## 快速工具路径

- 主动要求追奶、减奶或稳奶：`milk_assessment_evaluate(window_days=1, include_today=false)` -> 向妈妈确认 -> `milk_plan_preview`，并在 `options.prepared_assessment` 复用 24h 评估数据。
- 只说“泌乳计划/奶量管理计划/哪种适合我”：`milk_assessment_evaluate(window_days=7, include_today=false)` -> 必要时 `milk_snapshot_get` / `infant_growth_evaluate` -> 推荐计划类型并确认 -> `milk_plan_preview`。
- 有明确目标奶量、增奶量或减奶量：`milk_assessment_evaluate(window_days=1, include_today=false)` -> 向妈妈确认 -> `milk_plan_preview(target_daily_ml=... 或 delta_ml=...)`。
- 没有明确目标奶量：确认计划方向后直接 `milk_plan_preview`。
- 用户只想看状态，不要计划：`milk_snapshot_get` -> 需要时 `milk_assessment_evaluate`。
- 用户担心宝宝增长或摄入：`infant_growth_evaluate`，再决定是否计划。
- 用户要保存草稿：仅在上一次 `milk_plan_preview` 返回 `plan_preview_ready` 且 `data.validation.valid=true` 时，先确认保存方式，再 `milk_plan_mutate(operation="create")`。
- 用户要改草稿：先用 `milk_plan_preview` 重新生成或校验候选方案，通过并确认后再保存或更新。
- 用户要改已保存计划：`milk_plan_query(plan_id=...)` -> `milk_plan_preview(source_plan_id=...)` -> 确认后 `milk_plan_mutate(operation="update")`。

## 计划生成资格判断

调用 `milk_plan_preview` 前后都要遵守下面的判断。`milk_plan_preview` 返回 `plan_preview_not_recommended`、`plan_preview_needs_revision`、目标校验不通过或 `data.validation.valid=false` 时，不要继续调用 `milk_plan_mutate`，也不要自行编写计划。

避免重复评估：

- 如果已经调用过 `milk_assessment_evaluate`，调用 `milk_plan_preview` 时必须通过 `options.prepared_assessment` 传入该结果。
- 如果已经调用过 `infant_growth_evaluate`，调用 `milk_plan_preview` 时必须通过 `options.prepared_growth_assessment` 传入该结果。
- 不要在同一轮里先评估一次，又让 `milk_plan_preview` 对同一资料重新评估一次。

| 奶量评估 | 宝宝生长评估 | 动作 |
|---|---|---|
| 奶量偏高 | 生长正常 | 暂不生成追奶或减奶计划；建议观察 3-5 天，给出生活习惯与记录建议。若持续异常，再生成计划。 |
| 奶量正常 | 生长正常 | 可以生成稳奶计划。 |
| 奶量正常 | 生长异常 | 暂不生成追奶或减奶计划；提醒核查记录，询问近期是否生病、进食下降或测量误差，并建议关注/就医评估。 |
| 奶量异常 | 生长异常 | 立即生成对应追奶或减奶计划草稿，同时建议就医或 IBCLC/儿科评估。 |

说明：

- 奶量偏低通常对应 `increase_milk`。
- 奶量偏高通常对应 `decrease_milk`，但如果宝宝生长正常，应先观察，不要直接减奶。
- 奶量偏高且宝宝生长正常时，只有用户已观察 3-5 天并确认仍持续异常，才可在 `milk_plan_preview.options` 中传入 `{"observed_persistent_abnormal": true}` 继续生成减奶计划草稿。
- 一切正常时，不要生成追奶或减奶计划；只生成 `maintain_milk` 稳奶计划。
- 如果生长评估缺失或数据不足，说明不确定性，并优先补问或建议补充记录。

## 修改计划

用户修改计划时间表或目标后：

- 先调用 `milk_plan_preview(source_plan_id=...)` 或让 `milk_plan_mutate` 执行内部校验。
- 校验不通过时，只说明需要修改的点，不保存。
- 校验通过后，再请用户确认；用户确认后调用 `milk_plan_mutate(operation="update")` 或 `milk_plan_mutate(operation="create")`。
- 追奶、减奶、稳奶的修改边界由工具校验，不要绕过校验直接写入。

## 目标差异

追奶：

- 关注频率、夜间/清晨安排、记录奶量和宝宝有效摄入信号。
- 未知目标时，`milk_plan_preview` 负责生成默认追奶目标；用户主动提出目标奶量或增减量时，把目标作为 `target_daily_ml` 或 `delta_ml` 传入 `milk_plan_preview`。
- 默认追奶目标：不低于当前奶量 +50ml；如果超过 P85 参考上限，应提示风险并不建议。
- 每日产奶缺口 `<=300ml/天` 且当前吸奶+亲喂频率 `<8次/天`：目标频率为 8 次/天，新增次数为 `8 - 当前频率`。
- 每日产奶缺口 `>300ml/天`，或当前吸奶+亲喂频率 `8-9次/天`：目标频率为 10 次/天，新增次数为 `10 - 当前频率`，并安排 1 次吸奶。
- 新增吸奶优先放在昨日两次吸奶/亲喂之间较长空挡的中间，按开始时间计算。
- 常规新增吸奶为双侧同时吸奶 15 分钟。
- 该次吸奶只能安排在第 1-7 天，每天 1 次：吸 20 分钟 -> 休息 10 分钟 -> 吸 10 分钟 -> 休息 10 分钟 -> 吸 10 分钟；第 8 天后改为常规吸奶。
- 默认追奶计划按 1 个月生成。

稳奶：

- 生成稳奶计划前，先用自然、具体、不夸张的语言肯定妈妈当前已经形成的喂养/记录节奏；不要空泛夸奖，也不要让妈妈觉得必须做得更完美。
- 保持可执行节奏，避免过度增加任务。

减奶：

- 更谨慎，避免过快减少；如宝宝摄入或生长信号不明确，先建议补充信息或寻求专业建议。
- 减奶目标是每日减少量，用户明确提出目标奶量或减少量时，把目标作为 `target_daily_ml` 或 `delta_ml` 传入 `milk_plan_preview`；减少量下限 50ml/天，上限为当前参考日奶量。
- 未知目标但评估适合减奶时，`milk_plan_preview` 负责生成默认目标和阶段计划。
- 当前频次为吸奶+亲喂：`>=12次/天` 时先确认排除高泌乳素血症等病理因素，每 7 天减少 1 次；`3-12次/天` 且未满10月龄，每 7 天减少 1 次，优先减少每次时长并避免亲喂后用吸奶器；`>=10月龄且 <5次/天` 每 3 天减少 1 次；`<3次/天` 每 2 天减少 1 次。
- 后续阶段不应增加吸奶任务数量；每阶段通常只减少一个吸奶点。
- 减少时间段优先选择夜间非亲喂吸奶点，其次选择与前后间隔较短的吸奶点。
- 吸奶次数 `>=4` 时默认排期 1 个月；吸奶次数 `<4` 时按当前吸奶次数生成短周期阶段，最后阶段可仅做话术指导、不安排固定吸奶任务。
- 每次不要排空，胀满时只少量移出到舒适，并持续冷敷；出现胀痛、硬块、发烧或红肿加重时暂停普通计划流程。

## 安全提醒

出现发烧、严重乳房疼痛、红肿加重、宝宝脱水迹象、嗜睡或进食差时，不要继续普通计划流程，优先建议专业帮助。
