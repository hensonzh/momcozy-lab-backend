from __future__ import annotations

from typing import Any, Iterable, Literal, cast


ScheduleDomain = Literal["lactation", "pregnancy", "postpartum_recovery", "general"]

SCHEDULE_DOMAINS: frozenset[str] = frozenset(
    {
        "lactation",
        "pregnancy",
        "postpartum_recovery",
        "general",
    }
)
SCHEDULE_DOMAIN_ORDER: tuple[ScheduleDomain, ...] = (
    "lactation",
    "pregnancy",
    "postpartum_recovery",
    "general",
)

_PREGNANCY_PLAN_TYPES = frozenset({"pregnancy", "birth_prep", "birth_journey"})
_POSTPARTUM_RECOVERY_PLAN_TYPES = frozenset({"postpartum_recovery"})
SPECIALIZED_PLAN_TYPES = frozenset(
    {
        "milk_management",
        *_PREGNANCY_PLAN_TYPES,
        *_POSTPARTUM_RECOVERY_PLAN_TYPES,
    }
)


def schedule_domain_for_plan_type(plan_type: str) -> ScheduleDomain:
    normalized = plan_type.strip().lower()
    if normalized == "milk_management":
        return "lactation"
    if normalized in _PREGNANCY_PLAN_TYPES:
        return "pregnancy"
    if normalized in _POSTPARTUM_RECOVERY_PLAN_TYPES:
        return "postpartum_recovery"
    return "general"


def schedule_domain_for_task(*, plan_type: str | None, payload: dict[str, Any]) -> ScheduleDomain:
    if plan_type is not None:
        return schedule_domain_for_plan_type(plan_type)
    raw_domain = str(payload.get("domain") or "").strip().lower()
    return cast(ScheduleDomain, raw_domain) if raw_domain in SCHEDULE_DOMAINS else "general"


def schedule_event_type(payload: dict[str, Any]) -> str:
    return str(payload.get("event_type") or payload.get("task_type") or "task").strip().lower() or "task"


def normalize_schedule_domains(values: Iterable[str] | None) -> tuple[ScheduleDomain, ...]:
    if values is None:
        return SCHEDULE_DOMAIN_ORDER
    selected = {str(value).strip().lower() for value in values}
    if not selected or not selected.issubset(SCHEDULE_DOMAINS):
        raise ValueError("invalid_schedule_domains")
    return tuple(domain for domain in SCHEDULE_DOMAIN_ORDER if domain in selected)
