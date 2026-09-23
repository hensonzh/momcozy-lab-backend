from zoneinfo import ZoneInfo

from ..baby.models import BabyRecord
from ..baby.schemas import DailyStatusObservation, DevelopmentObservation, DiaperObservation, FeedingObservation, GrowthObservation, SleepObservation

COLORS = {'yellow': '黄色', 'yellow_brown': '黄褐色', 'green': '绿色', 'brown': '褐色', 'black': '黑色', 'red': '红色', 'pale': '灰白色', 'unsure': '不确定'}
CONSISTENCY = {'watery': '水样', 'loose': '稀软', 'pasty': '糊状', 'formed': '成形', 'hard': '颗粒或硬球', 'unsure': '不确定'}


def baby_record_text(value: BabyRecord, timezone: str) -> str:
    observation = value.observation
    lines = ['本服务关联宝宝的照护记录（用户填写）']
    if isinstance(observation, FeedingObservation):
        method = {'breastfeeding': '亲喂', 'expressed_milk': '母乳瓶喂', 'formula': '配方奶'}[observation.method]
        lines.append(f'喂养方式：{method}')
        if observation.method == 'breastfeeding':
            assert observation.side is not None
            lines.append(f'亲喂侧别：{ {"left": "左侧", "right": "右侧", "both": "两侧"}[observation.side]}')
            lines.append(f'亲喂时长：{str(observation.duration_minutes) + " 分钟" if observation.duration_minutes is not None else "未记录"}（不能换算为摄入量）')
        else:
            lines.append(f'实际记录的喂入量：{f"{observation.volume_ml:g} ml" if observation.volume_ml is not None else "未记录"}')
        if observation.note:
            lines.append(f'备注：{observation.note}')
    elif isinstance(observation, SleepObservation):
        zone = ZoneInfo(timezone)
        lines.append(f'入睡：{observation.occurred_at.astimezone(zone).isoformat()}')
        lines.append(f'醒来：{observation.ended_at.astimezone(zone).isoformat() if observation.ended_at else "尚未记录"}')
        if observation.note:
            lines.append(f'备注：{observation.note}')
    elif isinstance(observation, DiaperObservation):
        lines.append(f'尿布：{ {"wet": "尿湿", "dirty": "便便", "both": "尿湿和便便"}[observation.diaper_kind]}')
        if observation.diaper_kind != 'wet':
            lines.extend([f'颜色：{COLORS.get(observation.color or "", "未记录")}', f'性状：{CONSISTENCY.get(observation.consistency or "", "未记录")}'])
            lines.append('可见迹象：' + ('、'.join({'blood': '看到血迹', 'mucus': '看到黏液'}[sign] for sign in observation.signs) or '未填写'))
        if observation.note:
            lines.append(f'备注：{observation.note}')
    elif isinstance(observation, DailyStatusObservation):
        lines.append(f'记录日期：{observation.recorded_on}；记录时区：{observation.timezone}')
        if observation.mental_state is not None:
            state = {'content': '平静满足', 'active': '活跃', 'crying': '烦躁哭闹', 'drowsy': '困倦'}[observation.mental_state]
            lines.append(f'吃奶后精神状态：{state}')
        if observation.wet_count is not None:
            lines.append(f'今日湿尿布数：{observation.wet_count}')
        if observation.stool_count is not None:
            lines.append(f'今日便便次数：{observation.stool_count}')
            if observation.color is not None:
                lines.append(f'便便颜色：{COLORS[observation.color]}')
            if observation.consistency is not None:
                lines.append(f'便便性状：{CONSISTENCY[observation.consistency]}')
        lines.append('次数是该日填写的总数；同一天同一项目应以最新填写值为准，不累加多次提交。')
    elif isinstance(observation, GrowthObservation):
        lines.append(f'测量日期：{observation.recorded_on}；记录时区：{observation.timezone}')
        metric = {'weight': '体重', 'length': '身长', 'head_circumference': '头围'}[observation.metric]
        lines.append(f'测量：{metric} {observation.value:g} {observation.unit}')
    elif isinstance(observation, DevelopmentObservation):
        lines.append(f'观察日期：{observation.recorded_on}；记录时区：{observation.timezone}')
        status = {'observed': '观察到', 'not_observed': '尚未观察到', 'unsure': '不确定'}[observation.status]
        lines.append(f'具体行为：{observation.label}；本次观察：{status}（不是发育评估或能力结论）')
    return '\n'.join(lines)
