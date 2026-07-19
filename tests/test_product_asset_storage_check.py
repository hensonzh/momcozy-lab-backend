from __future__ import annotations

import asyncio

from app.infrastructure.object_storage import StoredObject
from app.modules.assets.models import ProductAsset
from scripts.check_product_asset_storage import check_manifest_assets


class FakeObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def put_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        self.objects[key] = body
        return StoredObject(key=key, uri=f"fake://{key}", size_bytes=len(body), content_type=content_type)

    async def get_bytes(self, *, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


def test_product_asset_storage_check_passes_when_manifest_objects_exist() -> None:
    result = asyncio.run(
        check_manifest_assets(
            storage=FakeObjectStorage({"product-assets/device-guidance/assets/air1/guide.png": b"1234"}),
            assets=[
                ProductAsset(
                    id="asset_guide",
                    label="Air1 guide",
                    domain="device_guidance",
                    content_type="image/png",
                    size_bytes=4,
                    object_key="product-assets/device-guidance/assets/air1/guide.png",
                )
            ],
        )
    )

    assert result.ok
    assert result.checked == 1
    assert result.to_payload()["status"] == "pass"


def test_product_asset_storage_check_reports_missing_manifest_object() -> None:
    result = asyncio.run(
        check_manifest_assets(
            storage=FakeObjectStorage({}),
            assets=[
                ProductAsset(
                    id="asset_missing",
                    label="Missing guide",
                    domain="device_guidance",
                    content_type="image/png",
                    size_bytes=4,
                    object_key="product-assets/device-guidance/assets/air1/missing.png",
                )
            ],
        )
    )

    assert not result.ok
    assert result.missing == ("product-assets/device-guidance/assets/air1/missing.png",)


def test_product_asset_storage_check_reports_size_mismatch() -> None:
    result = asyncio.run(
        check_manifest_assets(
            storage=FakeObjectStorage({"product-assets/device-guidance/assets/air1/guide.png": b"actual"}),
            assets=[
                ProductAsset(
                    id="asset_wrong_size",
                    label="Wrong size guide",
                    domain="device_guidance",
                    content_type="image/png",
                    size_bytes=4,
                    object_key="product-assets/device-guidance/assets/air1/guide.png",
                )
            ],
        )
    )

    assert not result.ok
    assert result.size_mismatches == ("product-assets/device-guidance/assets/air1/guide.png: expected 4, got 6",)
