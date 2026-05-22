# 待产包服务参考

当用户想要待产包、入院包、住院包、随身包、待产包卡片、入院物品准备，或问去医院前要带什么时，使用本参考文件。

## 服务定位

待产包服务不是让用户自己从长清单里筛东西，而是把她的孕周、分娩方式、医院信息、是否第一胎、喂养意向和支持人情况整理成一张可分享、可核对、可一键打包购物的待产包卡片。

LLM 只负责判断服务路径、做人话邀约、提取已知信息并下发工具指令。表单字段、字段顺序、卡片 schema、物品库、场景分包、数量、医院确认项、购物车 followup 和兼容字段都由工具稳定生成，不在本 reference 中展开。

## 进入与邀约

首次进入待产包服务时，按主 `SKILL.md` 的表单门控执行：先自然邀约，不直接创建表单。

邀约要短，贴用户当前场景：

- 临近生产或紧张：先稳一下，再说可以把“去医院要拿的东西”整理成待产包卡片。
- 普通准备：说明会快速确认几项信息，减少漏带、重复买和医院要求不一致。
- 已有信息会直接预填，用户只补缺的。

不要在邀约轮输出完整待产包清单，也不要用 markdown 长列表替代表单流程。

## 创建表单

用户确认开始后：

1. 优先复用当前对话、`request_context`、已提交表单或 `profile_get` 中的可靠信息。
2. 把可靠信息写入 `hospital_bag_form_create.default_values`。
3. 调用 `hospital_bag_form_create`。

`default_values` 是 JSON object string，只放确定信息，不要猜：

```json
{
  "default_values": "{\"due_date_or_week\":\"37周\",\"first_birth\":\"是\",\"birth_path\":\"顺产\",\"feeding_intention\":\"母乳\",\"birth_setting\":\"某某医院\",\"expected_stay\":\"2-3 天\",\"support_person\":\"有，且需要准备物品\",\"hospital_provided_items\":\"纸尿裤、产褥垫\"}"
}
```

可传字段由工具决定。常用字段包括：`due_date_or_week`、`first_birth`、`age`、`bmi_or_weight_context`、`pregnancy_history_or_notes`、`birth_path`、`expected_stay`、`support_person`、`birth_setting`、`hospital_provided_items`、`feeding_intention`。

表单标题、分类、选项、必填/选填和字段说明都由工具生成。不要再调用 `ui_form_create` 手写待产包字段。

## 生成卡片

看到 `confirmed_form_data form_id="hospital_bag_intake"` 后，除非必填答案明显冲突或存在急症信号，直接调用 `hospital_bag_card_create`。

调用方式：

```json
{
  "confirmed_form_data": "{}",
  "generation_mode": "standard"
}
```

当前用户消息已经包含完整 `confirmed_form_data` 时，工具会直接读取，不要把表单 JSON 复制进工具参数。只有在没有 `confirmed_form_data` 注入、但你确实掌握了用户确认后的结构化数据时，才传完整 JSON。

`generation_mode`：

- `standard`：默认。
- `quick`：用户拒绝详细确认，只要快速版。
- `immediate`：用户 37 周以后、马上去医院、快生了，且安全确认后适合生成即时可拿取版本。

不要让 LLM 自己生成待产包 `card_json`，也不要再调用 `ui_card_create` 手写待产包卡片。工具会生成：

- `hospital_bag_card` artifact。
- 按场景分包的 `packing_groups`。
- 医院确认项。
- 缺失字段和个性化说明。
- 购物车链接 followup。

## 工具结果后的最终回复

工具返回 `assistant_followup` 时，最终普通回复必须自然包含其中的购物车资源提示。不要把购物车链接写进 `card_json`。

回复要像把成果递给用户，而不是系统播报。可以说：

“你的待产包已经生成好了哦～我也顺手把适合直接购买的妈妈/宝宝用品整理成了购物车，方便你慢慢核对、删减。”

然后带上工具返回的链接文案。不要暗示用户必须购买，不要制造焦虑。

## 边界

- 不编造医院政策。
- 不建议用户自行携带药物或医疗设备，除非提醒其遵循医生/医院指导。
- 如果用户报告已经临产、大出血、严重疼痛、胎动减少或其他紧急症状，停止打包建议，优先建议联系医生、医院或急救服务。
- `pregnancy_history_or_notes` 只用于打包和医院确认提醒，不做医学判断。
