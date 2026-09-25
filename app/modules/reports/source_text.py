from typing import Any

from ..consultations.models import CareIntakeRevision
from ..lactation.models import LactationRecord

SYMPTOMS = {'latch_difficulty': 'Latching difficulties', 'feeding_pain': 'Pain during feeding', 'supply_concern': 'Milk supply concerns', 'frequent_waking': 'Frequent waking', 'pumping_schedule': 'Pumping schedule', 'other': 'Other'}
FEEDING = {'exclusive_breastfeeding': 'Exclusive breastfeeding', 'expressed_milk_feeding': 'Bottle-fed breast milk', 'mixed_feeding': 'Combination feeding', 'formula_feeding': 'Formula feeding'}


def lactation_text(value: LactationRecord) -> str:
    side = 'Left side' if value.side == 'left' else 'Right side'
    method = 'Pumping' if value.method == 'pump' else 'Nursing'
    measure = (
        f'Pumped milk: {"Not recorded" if value.volume_ml is None else f"{value.volume_ml:g} ml"} (not the baby’s intake)'
        if value.method == 'pump'
        else f'Nursing duration: {"Not recorded" if value.duration_minutes is None else str(value.duration_minutes) + " min"} (intake not measured)'
    )
    feeling = {'comfortable': 'Comfortable', 'full': 'Full', 'painful': 'Painful', 'uncertain': 'Not sure'}.get(value.feeling or '', 'Not recorded')
    return f'{method} record · {side}\n{measure}\nFeeling: {feeling}\nNotes: {value.note or "Not provided"}'


def intake_text(value: CareIntakeRevision) -> str:
    profile = value.profile
    return '\n'.join(['Consultation intake (client-reported)', f'Current concerns: {", ".join(SYMPTOMS[item] for item in value.symptoms)}',
        f'Desired change: {value.feeding_goal}', f'Support requested: {value.support_needed or "Not provided"}',
        f'Baby’s date of birth: {profile.get("baby_birth_date", "Not provided")}', f'Delivery date: {profile.get("delivery_date", "Not provided")}',
        f'Feeding method: {FEEDING.get(profile.get("feeding_mode", ""), "Not provided")}'])


def plan_text(content: dict[str, Any]) -> str:
    lines = [f'Published care plan: {content["title"]}', f'Summary: {content["summary"]}', 'Goals:']
    lines.extend(f'• {value}' for value in content['goals'])
    lines.append('Agreed tasks:')
    for value in content['tasks']:
        lines.append(f'• {value["title"]}: {value["description"]}; {value.get("scheduled_date") or value.get("due_label") or "No date scheduled"}')
    return '\n'.join(lines)
