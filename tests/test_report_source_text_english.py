import re
from datetime import date
from types import SimpleNamespace

from app.modules.baby.schemas import DailyStatusObservation
from app.modules.reports.baby_source_text import baby_record_text
from app.modules.reports.source_text import intake_text, lactation_text, plan_text


def test_generated_report_source_labels_are_english_without_changing_record_codes():
    lactation = lactation_text(SimpleNamespace(side="left", method="pump", volume_ml=60, duration_minutes=None,
                                                feeling="comfortable", note=""))
    intake = intake_text(SimpleNamespace(symptoms=["latch_difficulty"], feeding_goal="Less pain",
                                         support_needed="", profile={"feeding_mode": "mixed_feeding"}))
    plan = plan_text({"title": "Care plan", "summary": "Take one step", "goals": ["Comfort"], "tasks": [
        {"title": "Observe", "description": "Note changes", "due_label": "Next week"}]})
    baby = baby_record_text(SimpleNamespace(observation=DailyStatusObservation(
        kind="daily_status", recorded_on=date(2026, 9, 20), timezone="America/Los_Angeles",
        mental_state="content", wet_count=5, stool_count=2, color="yellow")), "America/Los_Angeles")
    for value in (lactation, intake, plan, baby):
        assert not re.search(r"[\u3400-\u9fff]", value), value
    assert "Pumped milk" in lactation and "not the baby’s intake" in lactation
    assert "Wet diapers today: 5" in baby
