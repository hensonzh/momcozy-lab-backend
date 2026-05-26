# 综合概览与分流

用于用户想整体看看近期奶量、吸奶、母乳、宝宝状态或风险，但还没有要求生成计划、调整日程或修改记录的场景。

## 边界

- 只做只读概览和下一步分流。
- 不生成追奶、稳奶、减奶计划。
- 不保存计划、创建提醒或写入 calendar。
- 有红旗症状时，停止概览，回到 `SKILL.md` 的红旗边界处理。

## 工具

需要工具时，先通过 `tool_search` 加载 `milk_management` namespace。

- 奶量/吸奶趋势：`milk_assessment_evaluate(window_days=7, include_today=false)`
- 宝宝生长、摄入是否相关：`infant_growth_evaluate`
- 状态页概览、30 日趋势或今日数据：`milk_status_query`

## 输出

- 只说 1-2 个关键结论和 1 个下一步。
- 可以提示风险，但不要下诊断式结论。
- 不把工具 `summary`、`advice` 或内部字段原样抛给用户。

用户想继续分析风险或调整方向时，读取 `milk-risk-intervention.md`；用户明确要求生成追奶/稳奶/减奶计划时，读取 `milk-plan-creation.md`。
