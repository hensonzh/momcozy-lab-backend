"""Historical Product tables retained by migrations after their API modules retired.

Alembic autogenerate must still compare every active ORM table. These named
legacy objects are intentionally left in historical databases; their removal
requires a separately reviewed data-retention migration, not CI drift cleanup.
"""

from __future__ import annotations

from typing import Any

RETAINED_TABLES = frozenset({
    "care_appointments",
    "care_booking_eligibility",
    "care_clinical_notes",
    "care_consent_revisions",
    "care_consultations",
    "care_conversation_links",
    "care_eligibility_checks",
    "care_episodes",
    "care_intake_revisions",
    "care_location_checks",
    "care_orders",
    "care_plan_drafts",
    "care_plan_publications",
    "care_provider_availability",
    "care_provider_calendar_blocks",
    "care_providers",
    "care_report_reviews",
    "care_reports",
    "care_room_participants",
    "care_service_event_reads",
    "care_service_events",
    "care_session_consumptions",
    "care_task_progress",
    "care_video_commands",
    "notification_event_receipts",
    "workbench_login_challenges",
    "workbench_mfa_credentials",
})
RETAINED_COLUMNS = frozenset({("device_sessions", "mfa_verified_at")})


def include_object(
    object_: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if not reflected or compare_to is not None:
        return True
    if type_ == "table" and name in RETAINED_TABLES:
        return False
    if type_ == "column" and (object_.table.name, name) in RETAINED_COLUMNS:
        return False
    return True
