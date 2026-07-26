from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import DiaryEntry


DIARY_CHANGED_EVENT = "diary.changed"


def diary_changed_payload(
    *,
    entry: DiaryEntry,
    operation: str,
    source: str,
) -> dict[str, Any]:
    normalized_operation = str(operation or "").strip()
    if normalized_operation not in {"created", "updated", "deleted"}:
        raise ValueError("Unsupported diary change operation.")
    updated_at = entry.updated_at if isinstance(entry.updated_at, datetime) else datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return {
        "operation": normalized_operation,
        "entry_id": str(entry.id),
        "entry_date": entry.entry_date.isoformat(),
        "updated_at": updated_at.isoformat(),
        "source": str(source or "").strip(),
    }
