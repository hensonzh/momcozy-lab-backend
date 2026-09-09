from datetime import date, datetime, time, timedelta, timezone
from collections.abc import Iterable
from zoneinfo import ZoneInfo


def slots_for_day(day: date, timezone_name: str, rules: Iterable[tuple[int, int, int, int]]) -> list[tuple[datetime, datetime]]:
    """Enumerate actual instants in the provider's calendar; discard nonexistent wall times.

    Ambiguous fall-back times are separate slots. Duration is elapsed real time.
    Repeated/overlapping weekly rules never produce duplicate instants.
    """
    zone = ZoneInfo(timezone_name)
    result = set()
    for weekday, first, last, duration in rules:
        if weekday != day.weekday():
            continue
        for minute in range(first, last - duration + 1, duration):
            wall = datetime.combine(day, time.min) + timedelta(minutes=minute)
            for fold in (0, 1):
                start = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                if start.astimezone(zone).replace(tzinfo=None) != wall:
                    continue
                end = start + timedelta(minutes=duration)
                wall_end = end.astimezone(zone).replace(tzinfo=None)
                limit = datetime.combine(day, time.min) + timedelta(minutes=last)
                if wall_end <= limit:
                    result.add((start, end))
    return sorted(result)
