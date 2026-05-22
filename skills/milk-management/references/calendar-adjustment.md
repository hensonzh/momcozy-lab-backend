# 日程调整流程

本 reference 使用的奶量管理工具都在 `milk_management` deferred namespace 中；如尚未加载，先通过 `tool_search` 加载该 namespace，再调用下文提到的具体工具。

适用于用户查询、新增、修改或删除奶量管理 calendar 内容，包括时间、条目内容、类型和完成状态。真实吸奶/喂养记录的新增、修改、删除使用 `milk_record_mutate`，不要用 calendar 工具代替。

## 原则

- calendar 可以独立使用，不依赖 milk plan。
- 只读查询可以直接调用工具；写操作必须先确认。
- 新增事项首轮只能 preview，不能直接 apply。
- 用户新增的事情必须写入 calendar；不能只调整吸奶/亲喂任务。
- preview / apply 的 payload 必须包含用户插入事件本身，以及由此产生的排奶调整。
- 涉及完成、取消完成或跳过任务时，优先使用 `milk_task_complete`；不要用 `milk_calendar_mutate` 直接写 `finish`。
- 只解释用户需要知道的变更，不暴露数据库字段或内部规则。

## 入口判断

1. 用户查询某天安排：走“查询日程”。
2. 用户查询一段时间内的计划执行情况：走“查询范围日程”。
3. 用户新增一个会占用时间的事项：走“新增事项并调整”。
4. 用户修改已有条目的时间、内容、类型或完成状态：走“修改已有条目”。
5. 用户批量顺延、删除或逐项调整某段时间内的计划：走“批量修改范围日程”。
6. 用户删除已有条目：走“删除条目”。

## 查询日程

1. 明确日期；如果用户没说日期，默认今天。
2. 调用 `milk_calendar_query(query_mode="range")`，日期按整天范围处理；如用户限定计划或类型，再传 `plan_id` / `item_type`。
3. 用简洁语言展示当天条目、时间、类型和完成状态。
4. 不调用写工具。

## 查询范围日程

适用于“过去一周计划完成怎么样”“查看未来三天安排”“今天下午到明天上午有哪些吸奶任务”等。

1. 明确起止时间；如果用户只给日期，按整天范围处理。
2. 调用 `milk_calendar_query(query_mode="range")`：
   - `user_id`
   - `start_at`
   - `end_at`
   - 如用户限定计划或类型，再传 `plan_id` / `item_type`
   - 只要统计时 `include_items=false`；需要定位具体条目时 `include_items=true`
3. 总结完成率、每日完成情况和关键未完成/待执行任务。
4. 不调用写工具。

## 新增事项并调整

适用于“下午要去吃饭”“9 点接孩子”“今天外出两小时”“临时加一次吸奶”等。

1. 如果事项时间不明确，先补问关键时间，不调用工具：
   - 事项日期。
   - 开始时间。
   - 结束时间或预计持续多久。
2. 识别插入事项类型：
   - 明确是吸奶/排奶/泵奶：`item_type="吸奶"`。
   - 明确是亲喂：`item_type="亲喂"`。
   - 吃饭、外出、开会、接孩子、上课、睡觉、通勤等：`item_type="自定义"`。
3. 时间确认后，调用 `milk_calendar_change_preview`：
   - `user_id`
   - `target_date`
   - `event_start_time`
   - `event_end_time` 或 `duration_minutes`
   - `content`
   - `item_type`
   - 如只调整某个计划，再传 `plan_id`
4. 向用户展示 preview 结果：
   - 将加入 calendar 的事项、日期、时间和类型。
   - 是否冲突。
   - 哪个吸奶/亲喂任务会调整。
   - 调整前后时间。
5. 询问用户是否确认。
6. 用户明确确认后，调用 `milk_calendar_mutate(operation="apply_adjustment")`：
   - `user_id`
   - `target_date`
   - `proposal`：必须传入 preview 返回的完整 `proposal` 或 `proposal_json`，不要自行丢弃 `insert_event` / `updates`
   - `idempotency_key`
7. 返回更新后的日程摘要，说明新增事项和被调整的吸奶/亲喂任务。
8. 如果 preview 显示没有冲突，仍然需要用户确认后再调用 `milk_calendar_mutate(operation="apply_adjustment")`，因为新增事项本身也需要写入 calendar。

## 修改已有条目

适用于用户修改某个 calendar 条目的时间、内容、类型或完成状态。

1. 如果用户没有明确目标条目，先调用 `milk_calendar_query(query_mode="range")` 查询当天或指定范围，必要时传 `item_type`。
2. 让用户确认要修改哪一条；不要凭模糊描述直接写。
3. 根据用户想改的字段构造 `patch`：
   - 改时间：`start_time`，必要时 `end_time`
   - 改内容：`content`
   - 改类型：`type` 或 `item_type`，值只能是 `吸奶`、`亲喂`、`自定义`
   - 改完成状态：走 `milk_task_complete`，不要在这里构造 `finish`
4. 用户确认后，调用 `milk_calendar_mutate(operation="update_item")`：
   - `user_id`
   - `item_id`
   - `patch`
   - `idempotency_key`
5. 如需展示最新日程，再调用 `milk_calendar_query(query_mode="range")`。

## 批量修改范围日程

适用于“把今天下午所有吸奶任务推迟 30 分钟”“删除明天上午的计划”“把未来三天这几条任务分别改到新时间”等。

1. 先明确起止时间、操作类型和影响范围；不明确时先补问。
2. 调用 `milk_calendar_query(query_mode="range")` 定位范围内条目。
3. 向用户说明将影响的条目数量、日期、类型和时间；询问确认。
4. 用户明确确认后，调用 `milk_calendar_mutate`：
   - `operation="range_shift"`：`patch={"shift_minutes": 30}` 或负数表示提前。
   - `operation="range_delete"`：删除范围内匹配条目，必须确保范围足够明确。
   - `operation="patch_items"`：先读取条目，再传 `patch={"updates":[{"item_id":..., "start_time":"09:00", "end_time":"09:30"}]}`。
5. 返回更新后的范围摘要。
6. 涉及完成状态 `finish` 的批量更新走“完成状态同步”。

## 完成状态同步

1. 用户要把条目标记为完成、取消完成或跳过时，先定位具体任务；如无法定位，调用 `milk_calendar_query(query_mode="range")` 展示候选并请用户确认。
2. 用户确认后调用 `milk_task_complete`：
   - 完成：`operation="complete"`
   - 取消完成：`operation="cancel_complete"`
   - 跳过：`operation="skip"`
3. 只有用户明确提供真实奶量或真实时长时，才传 `amount_ml` / `duration_minutes`；缺失时只更新完成状态，不要用计划值代替真实记录。
4. 确认全部完成时，先查询未完成任务并向用户说明将影响的数量；用户确认后逐项调用 `milk_task_complete(operation="complete", record_kind="none")`。
5. 不用 `milk_calendar_mutate` 直接写 `finish`，避免绕过记录同步逻辑。

## 删除条目

1. 如果用户没有明确目标条目，先调用 `milk_calendar_query(query_mode="range")` 查询当天或指定范围，必要时传 `item_type`。
2. 让用户确认要删除哪一条。
3. 用户明确确认后，调用 `milk_calendar_mutate(operation="delete_item")`：
   - `user_id`
   - `item_id`
   - `idempotency_key`
4. 如需展示最新日程，再调用 `milk_calendar_query(query_mode="range")`。

## 确认规则

可以视为确认：

- “确认”
- “可以”
- “好的”
- “就这样”
- “帮我改吧”

不能视为确认：

- 用户继续补充新时间。
- 用户问为什么。
- 用户要求换一个方案。
- 用户只是在闲聊或表达不确定。

## 重要限制

- 用户只说“下午要去吃饭”“晚点外出”时，不能直接 preview；必须先确认具体时间或持续时长。
- 用户确认前，不能调用 `milk_calendar_mutate`。
- 涉及 `finish` 的请求，即使用户确认，也暂时不能调用任何写工具，尤其不能调用 `milk_calendar_mutate`。
- 如果用户新增的是吃饭/外出等生活事项，必须作为 `自定义` 事项写入 calendar。
- 如果用户新增的是吸奶或亲喂，必须按对应类型写入 calendar。
