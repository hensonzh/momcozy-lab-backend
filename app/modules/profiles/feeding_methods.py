"""Canonical, user-editable feeding methods shared by setup and Me."""

from typing import Literal

FeedingMethod = Literal["direct", "expressed", "formula", "unknown"]
FEEDING_METHOD_ORDER: tuple[FeedingMethod, ...] = ("direct", "expressed", "formula", "unknown")


def validate_feeding_methods(methods: list[FeedingMethod]) -> list[FeedingMethod]:
    if len(methods) != len(set(methods)) or ("unknown" in methods and len(methods) != 1):
        raise ValueError("Select distinct feeding methods, or Not sure yet on its own")
    return [method for method in FEEDING_METHOD_ORDER if method in methods]


def feeding_mode_for_methods(methods: list[FeedingMethod]) -> str:
    selected = set(methods)
    if selected == {"unknown"}:
        return "unknown"
    if "formula" in selected:
        return "mixed_feeding" if len(selected) > 1 else "formula_feeding"
    if "direct" in selected:
        return "exclusive_breastfeeding"
    return "expressed_milk_feeding"
