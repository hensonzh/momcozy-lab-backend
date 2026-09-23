from typing import Any

from ..consultations.models import CareIntakeRevision
from ..lactation.models import LactationRecord

SYMPTOMS = {'latch_difficulty': '含乳困难', 'feeding_pain': '喂养疼痛', 'supply_concern': '奶量担忧', 'frequent_waking': '频繁夜醒', 'pumping_schedule': '泵奶安排', 'other': '其他'}
FEEDING = {'exclusive_breastfeeding': '纯母乳亲喂', 'expressed_milk_feeding': '母乳瓶喂', 'mixed_feeding': '混合喂养', 'formula_feeding': '配方奶喂养'}


def lactation_text(value: LactationRecord) -> str:
    side = '左侧' if value.side == 'left' else '右侧'
    method = '泵奶' if value.method == 'pump' else '亲喂'
    measure = f'泵出乳量：{"未记录" if value.volume_ml is None else f"{value.volume_ml:g} ml"}（不能据此判断宝宝摄入量）' if value.method == 'pump' else f'亲喂时长：{"未记录" if value.duration_minutes is None else str(value.duration_minutes) + " 分钟"}（未测量摄入量）'
    feeling = {'comfortable': '舒适', 'full': '胀满', 'painful': '疼痛', 'uncertain': '不确定'}.get(value.feeling or '', '未记录')
    return f'{method}记录 · {side}\n{measure}\n感受：{feeling}\n备注：{value.note or "未填写"}'


def intake_text(value: CareIntakeRevision) -> str:
    profile = value.profile
    return '\n'.join(['咨询前资料（用户自述）', f'当前困扰：{"、".join(SYMPTOMS[item] for item in value.symptoms)}',
        f'希望改善：{value.feeding_goal}', f'希望获得的帮助：{value.support_needed or "未填写"}',
        f'宝宝出生日期：{profile.get("baby_birth_date", "未填写")}', f'妈妈分娩日期：{profile.get("delivery_date", "未填写")}',
        f'喂养方式：{FEEDING.get(profile.get("feeding_mode", ""), "未填写")}'])


def plan_text(content: dict[str, Any]) -> str:
    lines = [f'已发布照护方案：{content["title"]}', f'摘要：{content["summary"]}', '目标：']
    lines.extend(f'• {value}' for value in content['goals'])
    lines.append('约定任务：')
    for value in content['tasks']:
        lines.append(f'• {value["title"]}：{value["description"]}；{value.get("scheduled_date") or value.get("due_label") or "未安排日期"}')
    return '\n'.join(lines)
