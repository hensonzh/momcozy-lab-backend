from pathlib import Path

import pytest

from app.core.errors import ApiError
from app.modules.assets import service as assets_service
from app.modules.assets.service import ProductAssetService


def test_default_product_asset_manifest_is_owned_by_production_backend() -> None:
    manifest_path = assets_service.DEFAULT_PRODUCT_ASSET_MANIFEST_PATH.as_posix()

    assert "/assets/product-assets.manifest.json" in manifest_path
    assert "/skills/" not in manifest_path


def test_product_asset_service_builds_allowlisted_manifest(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    (asset_root / "air1" / "images").mkdir(parents=True)
    (asset_root / "air1" / "images" / "guide.png").write_bytes(b"pngdata")
    (asset_root / "air1" / "images" / "notes.txt").write_text("not public")

    assets = ProductAssetService(root=asset_root).list_assets(limit=10)

    assert len(assets) == 1
    assert assets[0].id.startswith("asset_")
    assert assets[0].content_type == "image/png"
    assert assets[0].domain == "device_guidance"
    assert assets[0].path == asset_root / "air1" / "images" / "guide.png"
    assert assets[0].object_key is None


def test_product_asset_service_loads_manifest_without_local_asset_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "product-assets.manifest.json"
    manifest_path.write_text(
        """
        {
          "schema_version": "product_assets.v1",
          "assets": [
            {
              "id": "asset_manifest",
              "label": "Air1 guide",
              "domain": "device_guidance",
              "content_type": "application/pdf",
              "size_bytes": 1200,
              "object_key": "product-assets/device-guidance/assets/air1/guide.pdf"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    assets = ProductAssetService(manifest_path=manifest_path).list_assets(limit=10)

    assert len(assets) == 1
    assert assets[0].id == "asset_manifest"
    assert assets[0].path is None
    assert assets[0].object_key == "product-assets/device-guidance/assets/air1/guide.pdf"


def test_product_asset_service_rejects_unknown_asset_id(tmp_path: Path) -> None:
    service = ProductAssetService(root=tmp_path)

    with pytest.raises(ApiError) as exc_info:
        service.get_asset(asset_id="../../../skills/device-guidance/assets/air1/image.png")

    assert exc_info.value.code == "not_found"


def test_product_asset_service_validates_limit(tmp_path: Path) -> None:
    service = ProductAssetService(root=tmp_path)

    with pytest.raises(ApiError) as exc_info:
        service.list_assets(limit=0)

    assert exc_info.value.code == "validation_failed"
