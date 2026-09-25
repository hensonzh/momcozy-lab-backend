from zoneinfo import ZoneInfo

from ..baby.models import BabyRecord
from ..baby.schemas import DailyStatusObservation, DevelopmentObservation, DiaperObservation, FeedingObservation, GrowthObservation, SleepObservation

COLORS = {'yellow': 'Yellow', 'yellow_brown': 'Yellow-brown', 'green': 'Green', 'brown': 'Brown', 'black': 'Black', 'red': 'Red', 'pale': 'Pale', 'unsure': 'Not sure'}
CONSISTENCY = {'watery': 'Watery', 'loose': 'Loose', 'pasty': 'Pasty', 'formed': 'Formed', 'hard': 'Hard', 'unsure': 'Not sure'}


def baby_record_text(value: BabyRecord, timezone: str) -> str:
    observation = value.observation
    lines = ['Baby care record linked to this service (client-entered)']
    if isinstance(observation, FeedingObservation):
        method = {'breastfeeding': 'Nursing', 'expressed_milk': 'Bottle-fed breast milk', 'formula': 'Formula'}[observation.method]
        lines.append(f'Feeding method: {method}')
        if observation.method == 'breastfeeding':
            assert observation.side is not None
            lines.append(f'Nursing side: { {"left": "Left", "right": "Right", "both": "Both"}[observation.side]}')
            lines.append(f'Nursing duration: {str(observation.duration_minutes) + " min" if observation.duration_minutes is not None else "Not recorded"} (cannot be converted to intake)')
        else:
            lines.append(f'Recorded bottle amount: {f"{observation.volume_ml:g} ml" if observation.volume_ml is not None else "Not recorded"}')
        if observation.note:
            lines.append(f'Notes: {observation.note}')
    elif isinstance(observation, SleepObservation):
        zone = ZoneInfo(timezone)
        lines.append(f'Fell asleep: {observation.occurred_at.astimezone(zone).isoformat()}')
        lines.append(f'Woke up: {observation.ended_at.astimezone(zone).isoformat() if observation.ended_at else "Not recorded yet"}')
        if observation.note:
            lines.append(f'Notes: {observation.note}')
    elif isinstance(observation, DiaperObservation):
        lines.append(f'Diaper: { {"wet": "Wet", "dirty": "Dirty", "both": "Wet and dirty"}[observation.diaper_kind]}')
        if observation.diaper_kind != 'wet':
            lines.extend([f'Color: {COLORS.get(observation.color or "", "Not recorded")}', f'Consistency: {CONSISTENCY.get(observation.consistency or "", "Not recorded")}'])
            lines.append('Visible signs: ' + (', '.join({'blood': 'Visible blood', 'mucus': 'Visible mucus'}[sign] for sign in observation.signs) or 'Not provided'))
        if observation.note:
            lines.append(f'Notes: {observation.note}')
    elif isinstance(observation, DailyStatusObservation):
        lines.append(f'Record date: {observation.recorded_on}; time zone: {observation.timezone}')
        if observation.mental_state is not None:
            state = {'content': 'Calm and content', 'active': 'Alert and active', 'crying': 'Fussy or crying', 'drowsy': 'Drowsy'}[observation.mental_state]
            lines.append(f'Mood after feeding: {state}')
        if observation.wet_count is not None:
            lines.append(f'Wet diapers today: {observation.wet_count}')
        if observation.stool_count is not None:
            lines.append(f'Dirty diapers today: {observation.stool_count}')
            if observation.color is not None:
                lines.append(f'Stool color: {COLORS[observation.color]}')
            if observation.consistency is not None:
                lines.append(f'Stool consistency: {CONSISTENCY[observation.consistency]}')
        lines.append('Counts are totals for the recorded day. Use the latest entry for each item on the same day; do not add repeated submissions together.')
    elif isinstance(observation, GrowthObservation):
        lines.append(f'Measurement date: {observation.recorded_on}; time zone: {observation.timezone}')
        metric = {'weight': 'Weight', 'length': 'Length', 'head_circumference': 'Head circumference'}[observation.metric]
        lines.append(f'Measurement: {metric} {observation.value:g} {observation.unit}')
    elif isinstance(observation, DevelopmentObservation):
        lines.append(f'Observation date: {observation.recorded_on}; time zone: {observation.timezone}')
        status = {'observed': 'Observed', 'not_observed': 'Not observed yet', 'unsure': 'Not sure'}[observation.status]
        lines.append(f'Specific behavior: {observation.label}; this observation: {status} (not a developmental assessment or conclusion about ability)')
    return '\n'.join(lines)
