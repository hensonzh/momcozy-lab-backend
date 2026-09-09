from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ..consultations.models import CareConsentRevision


@dataclass(frozen=True)
class SharingWindow:
    start: datetime
    end: datetime | None


def _scope_windows(revisions: Sequence[CareConsentRevision], scope: str) -> list[SharingWindow]:
    result: list[SharingWindow] = []
    opened: datetime | None = None
    previous_at: datetime | None = None
    for revision in sorted((value for value in revisions if value.scope == scope), key=lambda value: value.version):
        # A clock adjustment cannot move a later grant back into a withdrawn period.
        at = max(previous_at, revision.recorded_at) if previous_at is not None else revision.recorded_at
        previous_at = at
        if revision.active and opened is None:
            opened = at
        elif not revision.active and opened is not None:
            if opened < at:
                result.append(SharingWindow(opened, at))
            opened = None
    if opened is not None:
        result.append(SharingWindow(opened, None))
    return result


def sharing_windows(revisions: Sequence[CareConsentRevision], *, linked_at: datetime) -> list[SharingWindow]:
    """Intersect explicit conversation association, case sharing and AI context."""
    cases, ai = _scope_windows(revisions, 'ibclc_case'), _scope_windows(revisions, 'ai_context')
    result: list[SharingWindow] = []
    left = right = 0
    while left < len(cases) and right < len(ai):
        case, context = cases[left], ai[right]
        start = max(linked_at, case.start, context.start)
        end = min(value for value in [case.end, context.end] if value is not None) if case.end is not None or context.end is not None else None
        if end is None or start < end:
            result.append(SharingWindow(start, end))
        if end is None:
            break
        if case.end == end:
            left += 1
        if context.end == end:
            right += 1
    return result
