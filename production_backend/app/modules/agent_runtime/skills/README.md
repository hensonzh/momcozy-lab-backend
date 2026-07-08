# Agent Service Skills

这里是模型侧服务能力的入口。

## 文件职责

- [catalog.py](catalog.py)：把一个服务场景整理成可读的 skill 清单，包括 playbook、可用工具、触发词、记忆范围和 action 策略。
- [playbooks.py](playbooks.py)：维护面向模型的服务流程文案，包括角色定位、回复风格、服务流程、交付物和边界。

## 维护规则

- 改某个场景“怎么服务用户”，优先改 `playbooks.py`。
- 改某个场景“能用哪些工具”，优先看 `catalog.py` 暴露出的 skill 清单，再回到 SDK profile 兼容层同步 tool allowlist。
- 新增场景时，先定义服务 skill，再接 routing、tool contract、eval；不要先写零散 prompt。

## 当前服务 Skill

- `general_assistant`：通用陪伴和导航。
- `pregnancy_service`：孕期计划、待产包、分娩沟通单。
- `lactation`：奶量、喂养、吸奶、泌乳计划和 IBCLC 支持。
- `postpartum_recovery`：产后恢复 check-in、轻量任务和日记。
- `after_sales`：设备指导、故障排查和售后工单。
- `safety_guardrail`：医疗红旗、情绪危机、权限和注入防护。
