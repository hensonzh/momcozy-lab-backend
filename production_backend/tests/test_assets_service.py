from pathlib import Path

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.assets import service as assets_service
from production_backend.app.modules.assets.service import ProductAssetService


def test_packaged_asset_root_is_owned_by_production_backend() -> None:
    asset_root = assets_service.PACKAGED_ASSET_ROOT.as_posix()

    assert "/production_backend/fixtures/product_assets/" in asset_root
    assert "/skills/" not in asset_root


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
