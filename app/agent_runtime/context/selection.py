from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable


@dataclass(frozen=True)
class ContextSelection:
    items: list[dict[str, Any]]
    item_refs: list[dict[str, Any]]
    estimated_tokens: int
    dropped_item_count: int


@dataclass(frozen=True)
class _Candidate:
    item: dict[str, Any]
    item_key: str
    item_type: str
    sequence: int
    source_index: int


@dataclass(frozen=True)
class _AtomicUnit:
    candidates: tuple[_Candidate, ...]
    complete: bool

    @property
    def newest_position(self) -> tuple[int, int]:
        return max(
            (candidate.sequence, candidate.source_index)
            for candidate in self.candidates
        )


def select_bounded_context_items(
    records: Iterable[Any],
    *,
    required_item_keys: set[str] | frozenset[str] = frozenset(),
    max_items: int,
    max_estimated_tokens: int,
) -> ContextSelection:
    """Select a recent context tail without splitting function-call pairs."""

    if max_items < 1:
        raise ValueError("max_items must be positive")
    if max_estimated_tokens < 1:
        raise ValueError("max_estimated_tokens must be positive")
    candidates = _normalize_candidates(records)
    units = sorted(_atomic_units(candidates), key=lambda unit: unit.newest_position)
    required_units = {
        index
        for index, unit in enumerate(units)
        if any(candidate.item_key in required_item_keys for candidate in unit.candidates)
    }
    selected_units = set(required_units)

    for index in range(len(units) - 1, -1, -1):
        if index in selected_units:
            continue
        unit = units[index]
        if not unit.complete:
            continue
        proposed_units = selected_units | {index}
        proposed = _flatten_units(units, proposed_units)
        if len(proposed) > max_items:
            break
        if estimate_json_tokens([candidate.item for candidate in proposed]) > max_estimated_tokens:
            break
        selected_units = proposed_units

    selected = _flatten_units(units, selected_units)
    if not selected:
        newest_complete = next(
            (unit for unit in reversed(units) if unit.complete),
            None,
        )
        selected = list(newest_complete.candidates) if newest_complete else []

    items = [dict(candidate.item) for candidate in selected]
    item_refs = [
        {
            "item_key": candidate.item_key,
            "item_type": candidate.item_type,
            "sequence": candidate.sequence,
            "position": position,
        }
        for position, candidate in enumerate(selected)
    ]
    return ContextSelection(
        items=items,
        item_refs=item_refs,
        estimated_tokens=estimate_json_tokens(items),
        dropped_item_count=max(0, len(candidates) - len(selected)),
    )


def estimate_json_tokens(value: Any) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    if not serialized:
        return 0
    return max(
        1,
        ceil(len(serialized) / 3),
        ceil(len(serialized.encode("utf-8")) / 4),
    )


def _normalize_candidates(records: Iterable[Any]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for source_index, record in enumerate(records):
        raw_item = getattr(record, "item", None)
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        raw_key = getattr(record, "item_key", None)
        item_key = (
            str(raw_key).strip()
            if isinstance(raw_key, str) and raw_key.strip()
            else f"legacy:{source_index}"
        )
        raw_type = getattr(record, "item_type", None)
        item_type = (
            str(raw_type).strip()
            if isinstance(raw_type, str) and raw_type.strip()
            else _item_type(item)
        )
        raw_sequence = getattr(record, "sequence", None)
        sequence = (
            raw_sequence
            if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool)
            else source_index + 1
        )
        candidates.append(
            _Candidate(
                item=item,
                item_key=item_key,
                item_type=item_type,
                sequence=sequence,
                source_index=source_index,
            )
        )
    return candidates


def _atomic_units(candidates: list[_Candidate]) -> list[_AtomicUnit]:
    grouped: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        call_id = _call_id(candidate.item)
        if candidate.item_type in {"function_call", "function_call_output"} and call_id:
            unit_key = f"function:{call_id}"
        else:
            unit_key = f"item:{candidate.item_key}:{candidate.source_index}"
        grouped.setdefault(unit_key, []).append(candidate)

    units: list[_AtomicUnit] = []
    for key, grouped_candidates in grouped.items():
        ordered = tuple(
            sorted(
                grouped_candidates,
                key=lambda candidate: (candidate.sequence, candidate.source_index),
            )
        )
        item_types = {candidate.item_type for candidate in ordered}
        complete = not key.startswith("function:") or {
            "function_call",
            "function_call_output",
        }.issubset(item_types)
        units.append(_AtomicUnit(candidates=ordered, complete=complete))
    return units


def _flatten_units(
    units: list[_AtomicUnit],
    selected_indexes: set[int],
) -> list[_Candidate]:
    return sorted(
        (
            candidate
            for index, unit in enumerate(units)
            if index in selected_indexes
            for candidate in unit.candidates
        ),
        key=lambda candidate: (candidate.sequence, candidate.source_index),
    )


def _item_type(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if isinstance(item_type, str) and item_type:
        return item_type
    return "message" if isinstance(item.get("role"), str) else "unknown"


def _call_id(item: dict[str, Any]) -> str:
    value = item.get("call_id")
    return str(value).strip() if value not in (None, "") else ""
