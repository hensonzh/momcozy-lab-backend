from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from app.core.errors import ApiError
from app.infrastructure.object_storage.base import ObjectStorage


PUMP_MODELS_OBJECT_KEY = "agent-references/device-service/pump-models.md"
PUMP_MODELS_REFERENCE_SCHEMA_VERSION = "pump_models.reference.v1"
PUMP_MODELS_RESULT_SCHEMA_VERSION = "pump-models.result.v1"
MAX_PUMP_MODELS_DOCUMENT_BYTES = 512 * 1024

_JSON_BLOCK_PATTERN = re.compile(r"```json[ \t]*\r?\n(?P<payload>.*?)\r?\n```", re.DOTALL)
_TOP_LEVEL_FIELDS = {"schema_version", "currency", "source_urls", "products"}
_PRODUCT_FIELDS = {
    "sku_id",
    "model",
    "name",
    "price_usd",
    "sale_price_usd",
    "tier",
    "use_cases",
    "preferences",
    "best_for",
    "features",
    "suction",
    "battery",
    "weight",
    "noise",
    "app",
    "supports_single_unit",
    "image_url",
    "source_url",
}
_REQUIRED_PRODUCT_TEXT_FIELDS = {
    "sku_id",
    "model",
    "name",
    "tier",
    "best_for",
    "suction",
    "battery",
    "noise",
    "image_url",
    "source_url",
}
_PRODUCT_LIST_FIELDS = {"use_cases", "preferences", "features"}


class PumpModelsReferenceService:
    def __init__(self, *, object_storage: ObjectStorage | None) -> None:
        self.object_storage = object_storage

    async def read(self) -> dict[str, Any]:
        if self.object_storage is None:
            raise _reference_unavailable()
        try:
            body = await self.object_storage.get_bytes(key=PUMP_MODELS_OBJECT_KEY)
        except Exception as exc:
            raise _reference_unavailable() from exc
        if len(body) > MAX_PUMP_MODELS_DOCUMENT_BYTES:
            raise _reference_invalid("document exceeds the size limit")
        try:
            markdown = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _reference_invalid("document must be UTF-8") from exc
        return parse_pump_models_markdown(markdown)

    async def tool_result(self) -> dict[str, Any]:
        return pump_models_result(await self.read())


def parse_pump_models_markdown(markdown: str) -> dict[str, Any]:
    blocks = _JSON_BLOCK_PATTERN.findall(markdown)
    if len(blocks) != 1:
        raise _reference_invalid("document must contain exactly one JSON code block")
    try:
        payload = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise _reference_invalid("JSON code block is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_FIELDS:
        raise _reference_invalid("top-level fields are invalid")
    if payload.get("schema_version") != PUMP_MODELS_REFERENCE_SCHEMA_VERSION:
        raise _reference_invalid("schema_version is unsupported")
    if payload.get("currency") != "USD":
        raise _reference_invalid("currency must be USD")

    source_urls = _https_url_list(payload.get("source_urls"), field="source_urls")
    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise _reference_invalid("products must be a non-empty array")
    products = [_validated_product(item, index=index) for index, item in enumerate(raw_products)]
    sku_ids = [product["sku_id"] for product in products]
    models = [product["model"].casefold() for product in products]
    if len(sku_ids) != len(set(sku_ids)):
        raise _reference_invalid("product sku_id values must be unique")
    if len(models) != len(set(models)):
        raise _reference_invalid("product model values must be unique")
    return {
        "schema_version": PUMP_MODELS_REFERENCE_SCHEMA_VERSION,
        "currency": "USD",
        "source_urls": source_urls,
        "products": products,
    }


def pump_models_result(reference: dict[str, Any]) -> dict[str, Any]:
    products = [_public_product(product) for product in reference["products"]]
    return {
        "schema_version": PUMP_MODELS_RESULT_SCHEMA_VERSION,
        "status": "models_ready",
        "currency": reference["currency"],
        "count": len(products),
        "products": products,
        "source_urls": list(reference["source_urls"]),
    }


def find_pump_product(products: list[dict[str, Any]], sku_id_or_model: str) -> dict[str, Any] | None:
    token = _match_key(sku_id_or_model)
    if not token:
        return None
    for product in products:
        aliases = (
            product["sku_id"],
            product["model"],
            product["name"],
            str(product["model"]).replace(" ", ""),
        )
        if token in {_match_key(alias) for alias in aliases}:
            return deepcopy(product)
    return None


def _validated_product(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PRODUCT_FIELDS:
        raise _reference_invalid(f"products[{index}] fields are invalid")
    product = dict(value)
    for field in _REQUIRED_PRODUCT_TEXT_FIELDS:
        product[field] = _required_text(product.get(field), field=f"products[{index}].{field}")
    weight = product.get("weight")
    if weight is not None and not isinstance(weight, str):
        raise _reference_invalid(f"products[{index}].weight must be a string or null")
    product["weight"] = weight.strip() if isinstance(weight, str) and weight.strip() else None
    product["price_usd"] = _price(product.get("price_usd"), field=f"products[{index}].price_usd")
    sale_price = product.get("sale_price_usd")
    product["sale_price_usd"] = (
        None if sale_price is None else _price(sale_price, field=f"products[{index}].sale_price_usd")
    )
    for field in _PRODUCT_LIST_FIELDS:
        product[field] = _string_list(product.get(field), field=f"products[{index}].{field}")
    for field in ("app", "supports_single_unit"):
        if not isinstance(product.get(field), bool):
            raise _reference_invalid(f"products[{index}].{field} must be a boolean")
    for field in ("image_url", "source_url"):
        if not product[field].startswith("https://"):
            raise _reference_invalid(f"products[{index}].{field} must be an HTTPS URL")
    return product


def _public_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku_id": product["sku_id"],
        "model": product["model"],
        "name": product["name"],
        "official_price": product["price_usd"],
        "sale_price": product["sale_price_usd"],
        "tier": product["tier"],
        "use_cases": list(product["use_cases"]),
        "preference_tags": list(product["preferences"]),
        "best_for": product["best_for"],
        "features": list(product["features"]),
        "suction": product["suction"],
        "battery": product["battery"],
        "weight": product["weight"],
        "noise": product["noise"],
        "app_supported": product["app"],
        "single_unit_available": product["supports_single_unit"],
        "image_url": product["image_url"],
        "source_url": product["source_url"],
    }


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _reference_invalid(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _reference_invalid(f"{field} must be a non-empty string array")
    result = [_required_text(item, field=field) for item in value]
    if len(result) != len(set(result)):
        raise _reference_invalid(f"{field} must not contain duplicates")
    return result


def _https_url_list(value: Any, *, field: str) -> list[str]:
    urls = _string_list(value, field=field)
    if any(not url.startswith("https://") for url in urls):
        raise _reference_invalid(f"{field} must contain only HTTPS URLs")
    return urls


def _price(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _reference_invalid(f"{field} must be a non-negative number")
    return float(value)


def _match_key(value: Any) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def _reference_unavailable() -> ApiError:
    return ApiError(
        code="pump_models_reference_unavailable",
        message="Pump model information is temporarily unavailable.",
        status=503,
    )


def _reference_invalid(reason: str) -> ApiError:
    return ApiError(
        code="pump_models_reference_invalid",
        message="Pump model information is invalid.",
        status=500,
        details={"reason": reason},
    )
