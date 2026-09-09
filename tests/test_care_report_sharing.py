from datetime import datetime, timedelta, timezone

from app.modules.consultations.models import CareConsentRevision
from app.modules.reports.sharing import SharingWindow, sharing_windows


def test_sharing_intersects_both_authorizations_and_never_backfills_withdrawn_periods():
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    def at(hour):
        return base + timedelta(hours=hour)
    def revision(scope, version, active, hour):
        return CareConsentRevision(scope=scope, version=version, active=active, recorded_at=at(hour), policy_version='2026-09-08')
    revisions = [revision('ibclc_case', 1, True, 0), revision('ai_context', 1, True, 1),
        revision('ibclc_case', 2, False, 3), revision('ibclc_case', 3, True, 5),
        revision('ai_context', 2, False, 7), revision('ai_context', 3, True, 9)]
    assert sharing_windows(revisions, linked_at=at(2)) == [SharingWindow(at(2), at(3)), SharingWindow(at(5), at(7)), SharingWindow(at(9), None)]
    assert sharing_windows(revisions, linked_at=at(8)) == [SharingWindow(at(9), None)]
    assert sharing_windows(revisions[:1], linked_at=at(0)) == []


def test_revision_order_is_authoritative_when_a_later_timestamp_moves_backwards():
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    def revision(scope, version, active, hour):
        return CareConsentRevision(scope=scope, version=version, active=active, recorded_at=base + timedelta(hours=hour), policy_version='2026-09-08')
    records = [revision('ibclc_case', 1, True, 0), revision('ai_context', 1, True, 0),
        revision('ai_context', 2, False, 3), revision('ai_context', 3, True, 2)]
    result = sharing_windows(list(reversed(records)), linked_at=base)
    assert result == [SharingWindow(base, base + timedelta(hours=3)), SharingWindow(base + timedelta(hours=3), None)]
