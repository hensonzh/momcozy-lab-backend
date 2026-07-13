from __future__ import annotations

import math
from typing import Any

from .workflow_reply import normalize_workflow_reply_context


_MAX_CART_GROUPS = 12
_MAX_CART_ITEMS = 120
_MAX_KEYWORDS = 20


def sanitize_agent_client_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    context: dict[str, Any] = {}
    for key, max_length in (
        ("source", 80),
        ("locale", 35),
        ("timezone", 80),
        ("message_sent_at", 80),
    ):
        text = _text(value.get(key), max_length=max_length)
        if text:
            context[key] = text

    cart = _hospital_bag_cart(value.get("hospital_bag_cart"))
    if cart is not None:
        context["hospital_bag_cart"] = cart
    workflow_reply = normalize_workflow_reply_context(value.get("workflow_reply"))
    if workflow_reply:
        context["workflow_reply"] = workflow_reply
    return context


def project_agent_client_context(value: Any) -> dict[str, Any]:
    context = sanitize_agent_client_context(value)
    projection: dict[str, Any] = {}
    for key in ("locale", "timezone", "message_sent_at", "hospital_bag_cart"):
        if key in context:
            projection[key] = context[key]
    return projection


def _hospital_bag_cart(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
        return None

    groups: list[dict[str, Any]] = []
    remaining_items = _MAX_CART_ITEMS
    for raw_group in value["groups"][:_MAX_CART_GROUPS]:
        if not isinstance(raw_group, dict):
            continue
        raw_items = raw_group.get("items")
        if not isinstance(raw_items, list):
            continue
        items = [_cart_item(item) for item in raw_items[:remaining_items]]
        items = [item for item in items if item is not None]
        remaining_items -= len(items)
        tone = _text(raw_group.get("tone"), max_length=16)
        groups.append(
            {
                "title": _text(raw_group.get("title"), max_length=120) or "待产包",
                "tone": tone if tone in {"rose", "mint", "sky"} else "rose",
                "items": items,
            }
        )
        if remaining_items <= 0:
            break

    return {
        "groups": groups,
        "totals": _cart_totals(value.get("totals")),
    }


def _cart_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    item_id = _text(value.get("id"), max_length=160)
    name = _text(value.get("name") or value.get("label"), max_length=240)
    if not item_id and not name:
        return None

    item: dict[str, Any] = {
        "id": item_id or name,
        "name": name or item_id,
        "desc": _text(value.get("desc"), max_length=1000),
        "qty": _positive_int(value.get("qty") or value.get("quantity"), default=1, maximum=999),
        "price": _number(value.get("price"), default=0),
    }
    keywords = value.get("keywords")
    if isinstance(keywords, list):
        normalized_keywords = [text for raw in keywords[:_MAX_KEYWORDS] if (text := _text(raw, max_length=120))]
        if normalized_keywords:
            item["keywords"] = normalized_keywords

    for key, max_length in (
        ("currency", 16),
        ("price_label", 80),
        ("sale_price_label", 80),
        ("product_url", 2048),
        ("image_url", 2048),
        ("image_alt", 240),
        ("sku_id", 160),
        ("model", 160),
    ):
        text = _text(value.get(key), max_length=max_length)
        if text:
            item[key] = text
    for key in (
        "official_price_usd",
        "sale_price_usd",
        "exchange_rate_usd_cny",
    ):
        number = _optional_number(value.get(key))
        if number is not None:
            item[key] = number
    return item


def _cart_totals(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    totals: dict[str, Any] = {}
    for key in (
        "subtotal",
        "discount",
        "shipping",
        "total",
        "exchange_rate_usd_cny",
        "converted_usd_subtotal",
    ):
        number = _optional_number(value.get(key))
        if number is not None:
            totals[key] = number
    item_count = value.get("item_count", value.get("itemCount"))
    if item_count is not None:
        totals["itemCount"] = _positive_int(item_count, default=0, maximum=9999, allow_zero=True)
    for key in ("currency",):
        text = _text(value.get(key), max_length=16)
        if text:
            totals[key] = text
    for key in ("mixed_currency",):
        if isinstance(value.get(key), bool):
            totals[key] = value[key]
    currency_totals = value.get("currency_totals")
    if isinstance(currency_totals, list):
        normalized = [_currency_total(item) for item in currency_totals[:8]]
        totals["currency_totals"] = [item for item in normalized if item]
    return totals


def _currency_total(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    total: dict[str, Any] = {}
    currency = _text(value.get("currency"), max_length=16)
    if currency:
        total["currency"] = currency
    for key in ("subtotal", "discount", "shipping", "total"):
        number = _optional_number(value.get(key))
        if number is not None:
            total[key] = number
    item_count = value.get("item_count", value.get("itemCount"))
    if item_count is not None:
        total["itemCount"] = _positive_int(item_count, default=0, maximum=9999, allow_zero=True)
    return total


def _text(value: Any, *, max_length: int) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""


def _number(value: Any, *, default: float) -> float:
    number = _optional_number(value)
    return number if number is not None else default


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return round(number, 2) if math.isfinite(number) else None


def _positive_int(value: Any, *, default: int, maximum: int, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    minimum = 0 if allow_zero else 1
    return max(minimum, min(maximum, int(value)))
