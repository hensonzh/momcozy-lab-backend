# 今日安排与日结流程

本 reference 使用的奶量管理工具都在 `milk_management` deferred namespace 中；如尚未加载，先通过 `tool_search` 加载该 namespace，再调用下文提到的具体工具。

适用于用户查看“今日计划、今日安排、今日日结、完成情况、顺延任务”。

## 今日安排

调用 `milk_calendar_query(query_mode="today_overview")` 读取当天 calendar，返回任务总数、已完成数、未完成数和任务列表。

如果用户问的不是今天，而是“过去几天/本周/未来几天”的计划执行情况，调用 `milk_calendar_query(query_mode="range")`，不要把多日问题拆成多次今日查询。

## 今日日结

调用 `milk_calendar_query(query_mode="today_summary")` 汇总当天 calendar 完成情况。

面向用户展示时只保留以下内容，不展开任务列表、吸奶记录、喂养记录、数据库字段或工具返回的完整 JSON：

```markdown
### 今日完成情况
- **总任务**：{total_count}项
- **已完成**：{completed_count}项
- **待完成**：{pending_count}项
- **完成率**：{completion_rate}%
- **吸奶计划任务**：{pump_task_count}项

最后用 1 句自然鼓励收尾。鼓励话术由你根据完成率和记录情况生成，不来自工具字段。
```

字段来源：

- `total_count`：`calendar_summary.total_count`
- `completed_count`：`calendar_summary.completed_count`
- `pending_count`：`calendar_summary.pending_count`
- `completion_rate`：`calendar_summary.completion_rate`
- `pump_task_count`：`calendar_summary.pump_task_count`

## 今日任务操作

- 标记完成：先确认具体任务。若用户已明确说“确认/就这个/帮我记上”，调用 `milk_task_complete(operation="complete")`。只有用户明确提供实际吸奶量、瓶喂量或亲喂/吸奶时长时，才传 `amount_ml` / `duration_minutes`；缺失时只更新完成状态，不要用计划时长代替真实记录。
- 取消完成：先确认具体任务；确认后调用 `milk_task_complete(operation="cancel_complete", delete_linked_record=true)`。如果用户明确要保留已同步的真实记录，`delete_linked_record=false`。
- 跳过任务：先确认具体任务；确认后调用 `milk_task_complete(operation="skip", delete_linked_record=true)`。
- 删除任务：确认后调用 `milk_calendar_mutate(operation="delete_item")`
- 新增事项并调整冲突：先 `milk_calendar_change_preview`，确认后 `milk_calendar_mutate(operation="apply_adjustment")`
- 顺延后续任务：先用 `milk_calendar_query(query_mode="range")` 定位范围，确认后调用 `milk_calendar_mutate(operation="range_shift")`
- 顺延或修改一段时间内的多项任务：先 `milk_calendar_query(query_mode="range")`，确认后 `milk_calendar_mutate`
- 确认全部完成：先用 `milk_calendar_query(query_mode="today_overview")` 定位未完成任务并向用户确认“将 X 项都标记完成”；用户确认后，对每个未完成任务调用 `milk_task_complete(operation="complete", record_kind="none")`，不要编造奶量或时长。

## 原则

- calendar 可独立使用，不依赖 milk_plan。
- 只读类工具可直接调用。
- 修改、删除、顺延、确认全部完成前必须取得用户明确确认。
- 涉及完成状态 `finish` 的更新使用 `milk_task_complete`，不要调用 `milk_calendar_mutate` 直接写 finish。
- 用户只说“完成了/这次完成”但无法定位任务时，先用 `milk_calendar_query(query_mode="today_overview")` 列出候选任务并请用户确认是哪一项。
