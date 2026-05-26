# 奶量计划创建流程

本 reference 用于生成、修改和保存追奶、稳奶或减奶计划。计划必须通过工具 preview 生成候选方案；保存计划会写入 `milk_plan` 并展开到 calendar，因此必须先让用户理解影响并明确确认。

## 适用场景

- 用户明确说“我要追奶”“帮我稳奶”“我要减奶”“做一个奶量管理计划”。
- 用户已完成风险前置对话，并确认想生成计划。
- 用户要修改、保存或删除已保存的奶量计划。

不适用：

- 用户只是想看今天怎么做：读取 `daily-lactation-checkin.md`。
- 用户只是担心奶量、宝宝是否吃饱或是否转奶粉：先读取 `milk-risk-intervention.md`。
- 用户只是查询记录、趋势或今日安排：使用对应查询工具或 `calendar-adjustment.md`。
- 出现红旗症状：停止计划流程，回到 `SKILL.md` 的红旗边界处理。

## 工具边界

需要工具时，先通过 `tool_search` 加载 `milk_management` namespace。

- `milk_assessment_evaluate`：评估近期奶量状态和记录完整性。
- `infant_growth_evaluate`：仅在宝宝增长、体重或摄入是否足够与计划决策相关时调用。
- `milk_plan_preview`：生成计划草稿，不写数据库，不写 calendar。
- `milk_plan_mutate`：保存、更新或删除用户已确认的计划；有副作用。
- `milk_plan_query`：读取已有计划。

具体计划算法、默认目标、任务展开、校验结果和 calendar 差异以工具返回为准。不要绕过工具自行编写完整计划或手工写 calendar。

## 标准流程

### 1. 明确计划方向

先确认计划类型：

- 追奶：`increase_milk`
- 稳奶：`maintain_milk`
- 减奶：`decrease_milk`

如果用户目标不清楚，先问 1 个最关键问题，例如“你现在更想增加奶量、稳住奶量，还是逐步减少？”

### 2. 评估基础数据

- 用户已明确计划方向：通常调用 `milk_assessment_evaluate(window_days=1, include_today=false)`，用于快速看最近完整 24 小时。
- 用户问“我适合什么计划”：调用 `milk_assessment_evaluate(window_days=7, include_today=false)`。
- 用户提到宝宝体重、增长、尿布、摄入是否足够：调用 `infant_growth_evaluate`。
- 如果工具提示关键数据不足，先补问或引导补记录，不继续生成计划。

已经有同一轮有效评估结果时，后续传给 `milk_plan_preview.options.prepared_assessment` 或 `prepared_growth_assessment`，不要重复评估。

### 3. 生成计划草稿

调用 `milk_plan_preview`。如果用户给出目标奶量、增奶量或减奶量，把目标传入工具；没有明确目标时，让工具按规则生成默认目标。

只有当 preview 返回可展示、可保存且校验通过时，才向用户展示草稿。若工具返回不推荐、需要修改或校验失败，只说明需要调整的点，不展示“确认保存”。

### 4. 展示草稿

展示内容只保留用户需要决定的部分：

- 计划类型和周期。
- 当前状态与目标。
- 每日关键安排。
- 观察指标。
- 安全边界。
- 保存后会写入 calendar 的影响。

不要逐字段复述工具 JSON。不要承诺奶量一定增加、稳定或减少。

### 5. 保存前确认

保存前必须说明：

- 将保存什么计划。
- 计划覆盖哪些日期。
- 会新增或调整多少 calendar 任务。
- 如果未来已有计划任务，用户要选择“追加到现有日程”还是“替换未来未完成计划任务”。

只有用户在看到上述影响后明确确认，才调用 `milk_plan_mutate(operation="create")` 或 `operation="update"`。

用户只是说“好/可以/继续”时，如果上一轮没有说明 calendar 影响和写入方式，不能视为保存确认。

## 修改和删除计划

- 修改已保存计划：先 `milk_plan_query` 读取目标计划，再 `milk_plan_preview(source_plan_id=...)` 或让工具校验候选方案。校验通过并确认后再写入。
- 删除计划：先说明会删除计划本身，以及是否删除关联 calendar 任务；用户确认后再调用 `milk_plan_mutate(operation="delete")`。
- 修改计划后是否重新展开 calendar，以工具 schema 和用户确认的影响范围为准。

## 计划类型边界

- 追奶、稳奶、减奶是当前工具支持的一等计划类型。
- 离乳、返工或混合喂养过渡可以作为用户目标和约束参与澄清，但不要声称工具有独立 plan type；需要时映射到减奶、稳奶或普通日程调整，并说明不确定性。
- 宝宝生长、尿布、精神状态或乳房症状提示专业风险时，不把计划作为替代医疗评估的方案。
