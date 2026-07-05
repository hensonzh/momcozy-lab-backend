from __future__ import annotations

import hashlib
from pathlib import Path

from ...core.errors import ApiError
from .models import ProductAsset


PRODUCTION_BACKEND_ROOT = Path(__file__).resolve().parents[3]
PACKAGED_ASSET_ROOT = (
    PRODUCTION_BACKEND_ROOT / "fixtures" / "product_assets" / "device-guidance" / "assets"
).resolve()
SUPPORTED_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ProductAssetService:
    def __init__(self, *, root: Path = PACKAGED_ASSET_ROOT) -> None:
        self.root = root.resolve()

    def list_assets(self, *, limit: int = 100) -> list[ProductAsset]:
        if limit < 1 or limit > 200:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 200.", status=422)
        return self._manifest()[:limit]

    def get_asset(self, *, asset_id: str) -> ProductAsset:
        normalized_id = str(asset_id or "").strip()
        if not normalized_id:
            raise ApiError(code="not_found", message="Asset not found.", status=404)
        for asset in self._manifest():
            if asset.id == normalized_id:
                return asset
        raise ApiError(code="not_found", message="Asset not found.", status=404)

    def _manifest(self) -> list[ProductAsset]:
        if not self.root.exists():
            return []
        assets: list[ProductAsset] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            content_type = _content_type(path)
            if not content_type:
                continue
            relative_path = path.relative_to(self.root).as_posix()
            assets.append(
                ProductAsset(
                    id=_asset_id(relative_path),
                    label=path.stem.replace("_", " ").replace("-", " ").strip(),
                    domain="device_guidance",
                    content_type=content_type,
                    size_bytes=path.stat().st_size,
                    path=path,
                )
            )
        return assets


def _asset_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]
    return f"asset_{digest}"


def _content_type(path: Path) -> str:
    return SUPPORTED_CONTENT_TYPES.get(path.suffix.lower(), "")
