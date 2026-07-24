import asyncio
import json

import pytest

from app.core.errors import ApiError
from app.agents.cozymate.tools.pump_models import (
    PUMP_MODELS_OBJECT_KEY,
    PumpModelsReferenceService,
    parse_pump_models_markdown,
)
from scripts.publish_pump_models_reference import publish_pump_models_reference
from scripts.check_pump_models_reference import check_pump_models_reference


def test_pump_models_reference_service_reads_the_fixed_object_key() -> None:
    storage = FakeObjectStorage(_reference_markdown().encode())
    service = PumpModelsReferenceService(object_storage=storage)

    reference = asyncio.run(service.read())

    assert storage.keys == [PUMP_MODELS_OBJECT_KEY]
    assert reference["currency"] == "USD"
    assert reference["products"][0]["model"] == "M9"
    assert reference["products"][0]["app"] is True


def test_pump_models_reference_service_does_not_fall_back_without_object_storage() -> None:
    service = PumpModelsReferenceService(object_storage=None)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.read())

    assert exc_info.value.code == "pump_models_reference_unavailable"
    assert exc_info.value.status == 503


def test_parse_pump_models_markdown_rejects_invalid_reference_schema() -> None:
    invalid = _reference_markdown(schema_version="pump_models.reference.v0")

    with pytest.raises(ApiError) as exc_info:
        parse_pump_models_markdown(invalid)

    assert exc_info.value.code == "pump_models_reference_invalid"
    assert exc_info.value.status == 500


def test_parse_pump_models_markdown_rejects_unstructured_markdown() -> None:
    with pytest.raises(ApiError) as exc_info:
        parse_pump_models_markdown("# Pump models\n\nM9 is a smart pump.")

    assert exc_info.value.code == "pump_models_reference_invalid"


def test_publish_pump_models_reference_validates_and_uploads_markdown(tmp_path) -> None:
    source_path = tmp_path / "pump-models.md"
    source_path.write_text(_reference_markdown(), encoding="utf-8")
    storage = FakeObjectStorage(b"")

    stored = asyncio.run(
        publish_pump_models_reference(
            storage=storage,
            source_path=source_path,
        )
    )

    assert stored.key == PUMP_MODELS_OBJECT_KEY
    assert storage.uploads == [
        {
            "key": PUMP_MODELS_OBJECT_KEY,
            "body": source_path.read_bytes(),
            "content_type": "text/markdown; charset=utf-8",
        }
    ]


def test_check_pump_models_reference_reads_and_validates_the_stored_document() -> None:
    storage = FakeObjectStorage(_reference_markdown().encode())

    result = asyncio.run(check_pump_models_reference(storage=storage))

    assert result == {
        "status": "pass",
        "object_key": PUMP_MODELS_OBJECT_KEY,
        "schema_version": "pump_models.reference.v1",
        "currency": "USD",
        "product_count": 1,
    }


class FakeObjectStorage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.keys: list[str] = []
        self.uploads: list[dict[str, object]] = []

    async def get_bytes(self, *, key: str) -> bytes:
        self.keys.append(key)
        return self.body

    async def put_bytes(self, *, key: str, body: bytes, content_type: str):
        self.uploads.append({"key": key, "body": body, "content_type": content_type})
        return FakeStoredObject(key=key, size_bytes=len(body), content_type=content_type)


class FakeStoredObject:
    def __init__(self, *, key: str, size_bytes: int, content_type: str) -> None:
        self.key = key
        self.uri = f"memory://{key}"
        self.size_bytes = size_bytes
        self.content_type = content_type


def _reference_markdown(*, schema_version: str = "pump_models.reference.v1") -> str:
    payload = {
        "schema_version": schema_version,
        "currency": "USD",
        "source_urls": ["https://momcozy.com/collections/wearable-breast-pump"],
        "products": [
            {
                "sku_id": "pump-m9",
                "model": "M9",
                "name": "Momcozy M9 Mobile Flow 智能吸奶器",
                "price_usd": 159.99,
                "sale_price_usd": 143.99,
                "tier": "pro_app",
                "use_cases": ["work_pumping", "portable"],
                "preferences": ["app", "performance"],
                "best_for": "上班、外出或高频吸奶。",
                "features": ["强吸力", "App 控制"],
                "suction": "最高 -300 mmHg",
                "battery": "约 4-5 次",
                "weight": "302 g",
                "noise": "≤42 dB",
                "app": True,
                "supports_single_unit": True,
                "image_url": "https://momcozy.com/m9.jpg",
                "source_url": "https://momcozy.com/collections/wearable-breast-pump",
            }
        ],
    }
    return "# Momcozy 吸奶器型号资料\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"
