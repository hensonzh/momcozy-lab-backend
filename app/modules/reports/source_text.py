from typing import Any

from ..consultations.models import CareIntakeRevision
from ..lactation.models import LactationRecord
from ..mother.models import MotherDiaryEntry

FIELDS = {
    'total': '总休息时长', 'interruptions': '夜间中断', 'longest_stretch': '最长连续休息', 'recovery': '醒后恢复感', 'day_rest': '白天休息',
    'resleep_difficulty': '再次入睡', 'disruptions': '休息被打断的原因', 'energy': '精力', 'discomfort_sites': '不适部位', 'severity': '不适程度',
    'impact': '对日常的影响', 'trend': '变化', 'urination': '排尿', 'bowel': '排便', 'note': '补充说明', 'tone': '心情', 'pressures': '压力来源', 'support': '支持感',
}
VALUES = {
    'under-3h': '少于 3 小时', '3-4h': '3–4 小时', '4-5h': '4–5 小时', '5-6h': '5–6 小时', '6h-plus': '6 小时以上', 'unknown': '不确定',
    'none': '无', '1-2': '1–2 次', '3-4': '3–4 次', '5-plus': '5 次以上', 'under-1h': '少于 1 小时', '1-2h': '1–2 小时', '2-3h': '2–3 小时', '3h-plus': '3 小时以上',
    'restored': '有所恢复', 'managing': '尚可应对', 'exhausted': '仍很疲惫', 'under-30m': '少于 30 分钟', '30-60m': '30–60 分钟', '60m-plus': '60 分钟以上',
    'easy': '较容易', 'somewhat-hard': '有些困难', 'hard': '明显困难', 'feeding': '喂养', 'baby': '照顾宝宝', 'discomfort': '身体不适', 'cannot-sleep': '难以入睡',
    'environment': '环境干扰', 'other': '其他', 'energized': '精力较好', 'depleted': '精力不足', 'lower-abdomen': '下腹', 'perineum': '会阴', 'c-section': '剖宫产切口',
    'back': '腰背', 'head-chest': '头部或胸部', 'mild': '轻微', 'noticeable': '较明显', 'hard-to-ignore': '难以忽略', 'some': '有一些', 'care-limited': '影响照顾自己或宝宝',
    'better': '有所好转', 'same': '差不多', 'worse': '有所加重', 'normal': '正常', 'leaking-urgency': '漏尿或尿急', 'painful-difficult': '疼痛或困难',
    'smooth': '顺畅', 'difficult': '不顺畅', 'painful-piles': '疼痛或痔疮困扰', 'steady': '平稳', 'tense': '紧张', 'low': '低落', 'reactive': '容易情绪波动', 'unclear': '说不清',
    'baby-worry': '担心宝宝', 'feeding-pressure': '喂养压力', 'body-recovery': '身体恢复', 'sleep-loss': '休息不足', 'family-friction': '家庭相处', 'self-doubt': '自我怀疑',
    'no-time': '缺少自己的时间', 'supported': '有人支持', 'carrying-most': '主要靠自己', 'alone': '感到孤单',
}
SYMPTOMS = {'latch_difficulty': '含乳困难', 'feeding_pain': '喂养疼痛', 'supply_concern': '奶量担忧', 'frequent_waking': '频繁夜醒', 'pumping_schedule': '泵奶安排', 'other': '其他'}
FEEDING = {'exclusive_breastfeeding': '纯母乳亲喂', 'expressed_milk_feeding': '母乳瓶喂', 'mixed_feeding': '混合喂养', 'formula_feeding': '配方奶喂养'}


def diary_text(value: MotherDiaryEntry) -> str:
    lines = [f'妈妈日记（用户自述） · {value.entry_date}']
    for group, label in [('rest', '休息'), ('body', '身体'), ('mood', '心情')]:
        fields = value.diary.get(group, {})
        lines.append(f'【{label}】')
        for key, item in fields.items():
            if item is None or item == '' or item == []:
                continue
            text = '、'.join(str(VALUES.get(part, part)) for part in item) if isinstance(item, list) else VALUES.get(item, item) if key != 'note' else item
            lines.append(f'{FIELDS[key]}：{text}')
    return '\n'.join(lines)


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
