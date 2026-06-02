# 分娩沟通单参考

当用户想要分娩沟通、生产偏好、分娩偏好、产房沟通、给医生/护士看的沟通内容、birth plan 或可分享计划时，使用本参考文件。

## 服务定位

分娩沟通单把用户最在意的沟通偏好、支持方式、宝宝出生后偏好、疼痛/麻醉沟通和医院待确认问题整理成一份适合产检、入院或产房交接时给医护团队看的沟通单。

它不是完整医疗计划，也不是要求医护团队必须执行的清单。

LLM 只负责判断路径、自然承接、提取可靠信息并调用工具。表单字段、字段顺序、选项、排他选项过滤、结构化 schema、强诉求降级、分区压缩和安全声明都由工具稳定生成。

## 创建表单

用户明确进入分娩沟通单服务后，不用聊天追问成长问卷，直接按主 `SKILL.md` 的表单门控执行。

创建表单时：

1. 优先复用当前对话、`request_context`、已提交表单或 `profile_get` 中的可靠信息。
2. 把可靠信息写入 `birth_plan_form_create.default_values`。
3. 调用 `birth_plan_form_create`。

`default_values` 是 JSON object string，只放确定信息，不要猜。常用字段包括：`due_date_or_week`、`birth_path`、`birth_setting`、`first_birth`、`top_priorities`、`support_person`、`communication_preferences`、`priority_notes`、`labor_preferences`、`intervention_preferences`、`pain_relief_preferences`、`pain_relief_notes`、`feeding_intention`、`baby_after_birth_preferences`、`if_plans_change`、`emergency_authorization`、`hospital_questions_focus`、`medical_notes`。

注意：

- `medical_notes` 默认不预填。只有用户明确表示“希望医护知道”的过敏、医生说明或医院限制，才放入 `medical_notes`。
- 如果历史字段里有 `support_people`，只作为兼容输入映射到 `support_person`，不要再创建 `support_people`。
- 表单字段由专用工具生成，不要手写分娩沟通单字段。

## 生成沟通单

看到 `confirmed_form_data form_id="birth_plan_card_intake"` 后，除非必填答案明显冲突或存在急症/不安全表达，直接调用 `labor_communication_card_create`。

当前用户消息已经包含完整 `confirmed_form_data` 时，工具参数传 `{}` 即可，不要把表单 JSON 复制进工具参数。没有应用侧注入时，不要调用沟通单生成工具，应先引导用户完成并提交对应表单。

不要让 LLM 自己生成分娩沟通单 `card_json`。`labor_communication_card_create` 会生成：

- 分娩沟通单 artifact。当前前端协议里仍使用 legacy `birth_plan_card` 作为 `card_type`。
- 适合前端渲染的紧凑 `card_json`。
- 沟通方式、疼痛缓解、宝宝出生后、计划变化时、提前问医院、医疗或安全信息等分区。
- 强硬表达的温和化沟通版本。
- 分娩沟通单使用提醒 followup。

## 工具结果后的最终回复

工具返回 `assistant_followup` 时，最终普通回复自然包含其中的使用提醒。不要重复输出沟通单里的全部内容。

回复要像把成果递给用户，而不是系统播报。只补一句这份沟通单适合什么时候给医生/护士看、能帮她说清楚什么。

## 边界

- 不把偏好写成医疗命令或必须执行的要求。
- 不提供手术、麻醉或医疗决策建议。
- 不根据其他字段推断诊断、风险分级或医学结论。
- 用户写出“拒绝、绝对不要、必须”等强诉求时，工具会降级成“希望先沟通/先了解/如果安全允许”的表达。
- 如果用户报告大出血、破水后不确定安全、胎动明显减少、严重腹痛、晕厥、胸痛或其他急症信号，停止沟通单流程，优先建议联系医生、医院或急救服务。
