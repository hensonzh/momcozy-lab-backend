---
name: milk-management
description: 用于奶量评估、吸奶/亲喂计划、追奶/稳奶/减奶计划、calendar 日程调整，以及基于用户、宝宝、喂养、吸奶、生长记录的泌乳问答。
safety_limits:
  - 不诊断疾病，也不保证奶量一定增加、稳定或减少。
  - 发热、严重乳房疼痛、红肿加重、宝宝脱水迹象、嗜睡或进食差时优先建议专业帮助。
  - 写入、修改、删除、顺延或确认完成前必须获得用户明确确认。
---

# 奶量管理

本 skill 是奶量管理的总入口。保持回答简洁；内部先判断用户意图，再进入对应 reference，不要在总入口重复分支细则。最终回复继承全局声音契约：奶量焦虑、自责、挫败、担心宝宝摄入或突然变化时，先接住她具体在担心什么，再解释查询、评估或计划的下一步。不能诊断疾病，也不能保证奶量一定增加、稳定或减少。

## 快速路由

按用户意图选择 reference 或工具入口。**遇到 briefing / 红旗 / 计划三类需要详细规则的场景时，必须用 `read_skill_file` 加载对应 reference 后再行动**，不要凭印象走流程。

### 综合健康概览（briefing）

触发：用户在新会话第一句或随后任何一轮表达综合概览意图。常见表达：

- **奶量/吸奶维度**："分析最近吸奶情况""分析一下最近吸奶""看下我的奶量""看下我的吸奶""最近吸奶怎么样""吸奶趋势怎么样""我奶量怎么样""帮我看看母乳""母乳分析""泌乳怎么样""我最近母乳够不够"等。
- **宝宝维度**："看下宝宝最近状态""宝宝长得怎么样""分析下宝宝""宝宝最近怎么样""帮我看看宝宝""宝宝吃得够不够""宝宝增长怎么样"等。
- **综合维度**："帮我看下我最近的情况""做个评估""看看我现在的状态""帮我看看母乳和宝宝""给我个 briefing""有没有需要注意的""看下我的数据""综合评估一下""有什么风险""最近有什么要注意"等。
- "分析最近吸奶情况" 是前端 quick-action chip 和"新建会话"首屏自动触发的标准入口，必须识别为 briefing 触发。

**行动**：调用 `read_skill_file(skill_id="milk-management", kind="references", path="briefing-and-fast-path.md")` 拿到完整 briefing + 追奶 fast-path 规则，按它的工具调用顺序和输出风格走流程。Briefing 之后若用户确认要追奶，按同一 reference 的 fast-path 流程，**不要**走 `milk-plan-creation.md` 的标准多步。

### 红旗症状响应（乳房 / 宝宝）

奶量管理的红旗判断服从全局安全策略。本 skill 只补充领域触发词和转介方向：

- **乳房红旗**：持续严重乳痛、发热、乳房红肿加重或硬块、脓性分泌物、寒战伴乳房症状、怀疑乳腺炎、突发剧烈疼痛、胸痛、呼吸困难。
- **宝宝红旗**：嗜睡或不易唤醒、24h 内尿布显著减少、尿色明显偏深、哭闹无泪或疑似脱水、持续呕吐或喷射性呕吐、拒奶、小月龄发热、发蓝发灰、呼吸困难、抽搐。

出现上述信号时，先按全局 3 段安全回应处理。乳房红旗优先建议今天/尽快联系医院产科、乳腺门诊或医生；宝宝红旗优先建议立刻联系儿科或儿童急诊，严重信号直接建议急救。IBCLC 可以作为哺乳衔接，但不能替代儿科或医生。

### 奶量场景的情绪承接

触发：用户表达"我奶不够""宝宝吃不饱""追奶卡住几天没起色""减奶不舍""堵奶+疲惫"等情绪化场景（**非红旗**）。

承接原则：

- 第一句先贴住她正在面对的具体事实和合理感受，不先解释原因。
- 不把第一轮变成医学清单、判断标准或数据追问；等她愿意继续时，只补问 1-2 个最少必要信息。
- 结尾只邀请一个最贴近当前问题的下一步，不塞多个选项。

如果想看 5 个高频场景的承接锚点示范，加载 `references/emotional-tone.md`。上面的承接原则不需要加载 reference 就要遵守。

### 具体功能性操作

- 创建追奶、稳奶、减奶、离乳或奶量管理计划（**非 briefing 后的 fast-path**）：读取 `references/milk-plan-creation.md`。
- 查看今日安排、今日日结、完成率、顺延或确认今日任务：读取 `references/today-summary.md`。
- 新增、修改、删除会影响吸奶/亲喂安排的事项：读取 `references/calendar-adjustment.md`。
- 查询任意时间段内的吸奶、亲喂、母乳瓶喂、奶粉瓶喂记录，或用户问"过去一周/最近几天母乳情况、记录、趋势"：只调用一次 `milk_records_query`，通常传 `summary_granularity="daily"`、`include_raw_records=false`。
- 新增、修改或删除真实吸奶/喂养记录：先用 `milk_records_query` 定位记录；用户确认后调用 `milk_record_mutate`。
- 查询状态页/当前奶量概览/今日数据/趋势/宝宝生长历史：调用 `milk_status_query`。
- 查询过去任意时间段计划执行情况：调用 `milk_calendar_query`。
- 批量修改当天或未来时间段的计划：先用 `milk_calendar_query` 定位范围和条目；用户确认后调用 `milk_calendar_mutate`。
- 标记任务完成、取消完成或跳过：先确认具体任务和是否同步真实记录；用户确认后调用 `milk_task_complete`。
- 问"够不够、偏低/偏高、是否正常、适合什么计划"：调用 `milk_assessment_evaluate`（已修复算法，combo 喂养场景下不再误触发 over-supply）。
- 问宝宝身高体重、增长、摄入是否影响喂养：调用 `infant_growth_evaluate`（返回 `trajectory_status` + `weight_history`，能识别 percentile 下沉趋势）。
- 新增、修改或更新今日宝宝身高体重头围：用户确认后调用 `infant_growth_mutate`；如用户要判断增长是否正常，写入后再调用 `infant_growth_evaluate`。
- 问已有计划：调用 `milk_plan_query`。

## 通用原则

- 优先使用奶量管理专用工具；不要为了奶量计划调用设备工具。
- 奶量管理流程不能覆盖全局人格和语气。数据解释、计划草稿和写入确认的最终回复里，都要保留"先接住人，再处理事"的顺序。
- 本 skill 继承全局"服务路径连续性协议"：如果当前已经形成一个单一、明确的奶量管理服务路径，用户用"好/可以/继续/帮我做/就这个"等短确认接受，应沿着当前路径继续，不要退回普通科普或重新列建议。
- 工具只提供结构化事实、规则计算、边界校验、候选计划或写入结果；不要把工具返回的 `summary`、`message`、`advice` 直接当作最终回复。
- 用户意图判断、是否补问、如何解释不确定性、鼓励/安抚话术和最终建议由你完成；工具不负责生成最终对话。
- `*_get` / `*_query` 只读取事实；`*_evaluate` 只做基于参考数据的规则化评估；`*_preview` 只返回候选方案；`*_mutate` 才能写入。
- 只补问关键缺失信息，不展开长问卷。
- 已经调用过评估工具时，后续生成计划要通过 `options.prepared_assessment` / `options.prepared_growth_assessment` 复用结果，避免重复评估。
- 已经调用过 `milk_records_query` 时，同一轮不要用相同 `start_at` / `end_at` / `record_scope` 再调用一次；复用前一次结果。只有需要原始记录 ID 做修改/删除，且上一次没有 `include_raw_records=true` 时，才允许再次查询原始记录。
- 历史记录汇总和规则评估不要默认混用：用户只问"过去一周母乳情况/记录/趋势/每天多少"时，用 `milk_records_query` 即可；用户明确问"是否正常/够不够/偏低偏高/制定计划"时，才额外使用 `milk_assessment_evaluate`。
- 工具已返回 plan draft 时，不要自行重写计划结构；只总结目标、阶段、每日安排、观察点和安全提醒。
- 只读工具可直接调用；写入、修改、删除、顺延、确认完成前必须获得用户明确确认。
- 真实记录和计划日程要分清：`milk_record_mutate` 修改 `feeding_log` / `pumping_log`；`milk_calendar_mutate` 修改计划 calendar。
- 任务完成状态用 `milk_task_complete`，不要通过 `milk_calendar_mutate` 直接写 finish。
- 任务完成时，只有用户明确提供实际吸奶量、瓶喂量或亲喂/吸奶时长，才把这些值传给 `milk_task_complete` 同步真实记录；不要用计划时长或计划内容代替真实记录数据。
- 亲喂奶量无法从记录直接精确得出；工具若返回估算字段（如 `estimated_breastfeeding_ml`、`total_milk_estimate`），最终回复必须说明这是估算，不要当作实测值。
- 出现发烧、严重乳房疼痛、红肿加重、宝宝脱水迹象、嗜睡或进食差时，暂停普通计划流程，按"红旗症状响应"reference 走。

## 服务路径连续性

奶量管理里的短确认要根据当前服务路径的动作类型处理：

- 如果当前服务路径是只读查询、今日总结、趋势复盘、奶量评估或宝宝生长评估，用户短确认后，调用对应只读或评估工具，并用简短解释承接结果。
- 如果当前服务路径是制定追奶、稳奶、减奶、返工或离乳计划，用户短确认后，先补齐必要缺失信息；信息足够时调用 `milk_plan_preview` 生成草稿。草稿不等于已保存计划。
- 如果当前服务路径是调整日程、标记完成、保存/修改/删除记录、写入宝宝生长数据或保存计划，只有当当前路径已经明确说明具体对象、时间范围和影响时，短确认才可作为执行确认；否则先复述即将修改的内容并再次请求明确确认。
- 如果真的并列提供多个奶量管理选项，例如"看今日总结/做趋势复盘/制定计划"，且没有清晰主路径，用户只回复短确认时，先问她要先处理哪一个。
- 任何红旗症状或宝宝危险信号都优先于服务承接。

## 常用工具

只读：

- `milk_snapshot_get`
- `milk_status_query`
- `milk_assessment_evaluate`
- `infant_growth_evaluate`
- `milk_records_query`
- `milk_calendar_query`
- `milk_plan_query`

计划：

- `milk_plan_preview`
- `milk_plan_mutate`

记录：

- `milk_record_mutate`
- `infant_growth_mutate`

日程：

- `milk_calendar_query`
- `milk_calendar_change_preview`
- `milk_calendar_mutate`
- `milk_task_complete`

## 写操作确认

以下工具必须先获得用户明确确认：

- `milk_plan_mutate`
- `milk_record_mutate`
- `milk_calendar_mutate`
- `milk_task_complete`
- `infant_growth_mutate`

可以视为确认：用户明确说"确认""可以""好的""就这样""帮我保存/修改/执行"。  
不能视为确认：用户继续补充条件、询问原因、要求换方案、表达不确定或只是闲聊。

保存奶量计划前，上一次 `milk_plan_preview` 必须返回 `plan_preview_ready` 且 `data.validation.valid=true`。如果 preview 返回需要修改或校验未通过，不展示"确认保存"，先重新生成或调整草稿。
