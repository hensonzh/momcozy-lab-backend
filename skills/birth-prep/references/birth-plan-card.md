# 分娩沟通卡参考

当用户想要分娩计划、生产偏好、分娩偏好、产房沟通卡、给医生/护士看的沟通卡、birth plan 或可分享计划卡片时，使用本参考文件。

## 服务定位

分娩沟通卡不是完整医疗计划，也不是让用户写一份强制医护执行的清单，而是把她最在意的沟通偏好、支持方式、宝宝出生后偏好、疼痛/麻醉沟通和医院待确认问题整理成一张适合产检、入院或产房交接时给医护团队看的沟通卡。

LLM 只负责判断服务路径、做人话承接、提取已知信息并下发工具指令。表单字段、字段顺序、选项、排他选项过滤、卡片 schema、强诉求降级、分区压缩和安全声明都由工具稳定生成，不在本 reference 中展开。

## 创建表单

用户明确进入分娩沟通卡服务后，按主 `SKILL.md` 的表单门控执行，不要用多轮聊天追问长问卷。

创建表单时：

1. 优先复用当前对话、`request_context`、已提交表单或 `profile_get` 中的可靠信息。
2. 把可靠信息写入 `birth_plan_form_create.default_values`。
3. 调用 `birth_plan_form_create`。

`default_values` 是 JSON object string，只放确定信息，不要猜：

```json
{
  "default_values": "{\"due_date_or_week\":\"37周\",\"birth_path\":\"顺产\",\"birth_setting\":\"某某医院\",\"first_birth\":\"是\",\"support_person\":\"伴侣陪产并参与重要决定\",\"feeding_intention\":\"母乳喂养\"}"
}
```

可传字段由工具决定。常用字段包括：`due_date_or_week`、`birth_path`、`birth_setting`、`first_birth`、`top_priorities`、`support_person`、`communication_preferences`、`priority_notes`、`labor_preferences`、`intervention_preferences`、`pain_relief_preferences`、`pain_relief_notes`、`feeding_intention`、`baby_after_birth_preferences`、`if_plans_change`、`emergency_authorization`、`hospital_questions_focus`、`medical_notes`。

注意：

- `medical_notes` 默认不预填。只有用户明确表示“希望医护知道”的过敏、医生说明或医院限制，才放入 `medical_notes`。
- 如果历史字段里有 `support_people`，只作为兼容输入映射到 `support_person`，不要再创建 `support_people`。
- 不要再调用 `ui_form_create` 手写分娩沟通卡字段。

## 生成卡片

看到 `confirmed_form_data form_id="birth_plan_card_intake"` 后，除非必填答案明显冲突或存在急症/不安全表达，直接调用 `birth_plan_card_create`。

调用方式：

```json
{
  "confirmed_form_data": "{\"due_date_or_week\":\"37周\",\"birth_path\":\"顺产\",\"top_priorities\":[\"宝宝出生后，想尽早抱一抱/贴一贴\",\"希望伴侣/支持人尽量陪在身边\"],\"communication_preferences\":[\"做操作前，先告诉我为什么需要\",\"重要决定也请同步伴侣/支持人\"]}"
}
```

不要让 LLM 自己生成分娩沟通卡 `card_json`，也不要再调用 `ui_card_create` 手写分娩沟通卡。工具会生成：

- `birth_plan_card` artifact。
- 适合前端渲染的紧凑 `card_json`。
- 沟通方式、疼痛缓解、宝宝出生后、计划变化时、提前问医院、医疗或安全信息等分区。
- 强硬表达的温和化沟通版本。
- 分娩沟通卡使用提醒 followup。

## 工具结果后的最终回复

工具返回 `assistant_followup` 时，最终普通回复必须自然包含其中的使用提醒。不要重复输出卡片里的全部内容。

回复要像把成果递给用户，而不是系统播报。可以说：

“你的分娩沟通卡已经整理好了哦～这张卡可以在产检或入院前给医生/护士看，帮你更快说清楚哪些事最希望被解释、确认和支持。”

## 边界

- 不把偏好写成医疗命令或必须执行的要求。
- 不提供手术、麻醉或医疗决策建议。
- 不根据其他字段推断诊断、风险分级或医学结论。
- 用户写出“拒绝、绝对不要、必须”等强诉求时，工具会降级成“希望先沟通/先了解/如果安全允许”的表达。
- 如果用户报告大出血、破水后不确定安全、胎动明显减少、严重腹痛、晕厥、胸痛或其他急症信号，停止卡片流程，优先建议联系医生、医院或急救服务。
